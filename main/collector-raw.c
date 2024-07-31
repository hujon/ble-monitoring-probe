#include <stdio.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "esp_bt.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"

#include "bt_hci_common.h"

#include "driver/uart.h"

#define HCI_EVENT_MAX_SIZE (3 + 255) // 3 octet header + 255 bytes of data [Vol. 4, Part E, 5.4]
#define HCI_BUFFER_SIZE 10  // Empirically tested that ESP manages to process messages fast enough,
                            // that 3 items are mostly sufficient

// Logging tag
static const char *TAG = "BLE AD SCANNER";

// Channel to monitor
static const uint8_t CHANNEL = 37;

// UART settings
const uart_port_t uart_num = UART_NUM_0;
uart_config_t uart_config = {
    .baud_rate = 115200,
    .data_bits = UART_DATA_8_BITS,
    .parity = UART_PARITY_DISABLE,
    .stop_bits = UART_STOP_BITS_1,
    .flow_ctrl = UART_HW_FLOWCTRL_DISABLE
};

static QueueHandle_t uart_queue;

// Espressif supplied function for customization of BLE scan channel selection
// Location: vendor/libbtdm_app.a
extern void btdm_scan_channel_setting(uint8_t channel);

typedef struct {
    int64_t timestamp;
    uint16_t len;
    uint8_t *data;
} hci_data_t;

static QueueHandle_t adv_queue;

// Buffer for HCI events; 
static uint8_t *hci_buffer = NULL;
static uint8_t hci_buffer_idx = 0;

/*
 * @brief: Callback function of Bluetooth controller used to notify that the controller has a packet to send to the host.
 */
static int controller_out_rdy(uint8_t *data, uint16_t len)
{
    hci_data_t queue_data;
    queue_data.timestamp = esp_timer_get_time();  // Get microseconds since ESP boot

    if (len > HCI_EVENT_MAX_SIZE) {
        ESP_LOGD(TAG, "Packet too large.");
        return ESP_FAIL;
    }
    if (uxQueueMessagesWaitingFromISR(adv_queue) >= HCI_BUFFER_SIZE) {
        ESP_LOGD(TAG, "Failed to enqueue advertising report. Queue full.");
        return ESP_FAIL;
    }
    uint8_t* packet = hci_buffer + hci_buffer_idx * HCI_EVENT_MAX_SIZE;
    hci_buffer_idx = (hci_buffer_idx + 1) % HCI_BUFFER_SIZE;
    memcpy(packet, data, len);

    queue_data.data = packet;
    queue_data.len = len;
    if (xQueueSendToBackFromISR(adv_queue, (void*)&queue_data, NULL) != pdTRUE) {
        ESP_LOGD(TAG, "Failed to enqueue advertising report. Queue full.");
    }

    return ESP_OK;
}

static esp_vhci_host_callback_t vhci_host_cb = {
    NULL,
    controller_out_rdy
};

void hci_evt_process(void *pvParameters)
{
    hci_data_t* hci_data = (hci_data_t*)malloc(sizeof(hci_data_t));
    if (hci_data == NULL) {
        ESP_LOGE(TAG, "Cannot allocate heap for HCI data.");
        return;
    }
    memset(hci_data, 0, sizeof(hci_data_t));

    while (1) {
        if (xQueueReceive(adv_queue, hci_data, portMAX_DELAY) != pdTRUE) {
            ESP_LOGE(TAG, "Error while receiving a packet from HCI queue.");
            continue;
        }

//  Text format:
//        esp_rom_printf("Adv:");
//        esp_rom_printf("%lld,%u,", hci_data->timestamp, hci_data->len);
//        for (uint8_t i = 0; i < hci_data->len; i++) {
//            esp_rom_printf("%02x", hci_data->data[i]);
//        }
//        esp_rom_printf("\n");

        uart_write_bytes(uart_num, "BLE:", 4);
        uart_write_bytes(uart_num, (const char*)&hci_data->timestamp, 8);
        uart_write_bytes(uart_num, (const char*)&hci_data->len, 2);
        uart_write_bytes(uart_num, (const char*)hci_data->data, hci_data->len);
        uart_wait_tx_done(uart_num, portMAX_DELAY);

        memset(hci_data->data, 0, HCI_EVENT_MAX_SIZE);
    }

    free(hci_data);
}

void app_main(void)
{
    esp_err_t errCode = ESP_OK;
    static uint8_t hci_message[HCI_EVENT_MAX_SIZE];

    /* Transmit the startup time */
    int64_t start_time = esp_timer_get_time();
    esp_rom_printf("Capture started at: %lu\n", (unsigned long)(start_time / 1000));


    /* Initialise NVS - used to store PHY calibration data */
    errCode = nvs_flash_init();
    if (errCode == ESP_ERR_NVS_NO_FREE_PAGES || errCode == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        errCode = nvs_flash_init();
    }
    ESP_ERROR_CHECK(errCode);

    /* Configure UART */
    ESP_ERROR_CHECK(uart_param_config(uart_num, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(uart_num, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(uart_num, HCI_BUFFER_SIZE * HCI_EVENT_MAX_SIZE, HCI_BUFFER_SIZE * HCI_EVENT_MAX_SIZE, HCI_BUFFER_SIZE, &uart_queue, 0));

    /* Initialise Bluetooth */
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();

    /* Release the heap of Bluetooth Classic as we won't need it */
    errCode = esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    if (errCode != ESP_OK) {
        ESP_LOGI(TAG, "Bluetooth controller release BT CLASSIC memory failed: %s", esp_err_to_name(errCode));
        return;
    }

    /* Set up Bluetooth resources */
    errCode = esp_bt_controller_init(&bt_cfg);
    if (errCode != ESP_OK) {
        ESP_LOGE(TAG, "Bluetooth controller initialisation failed: %s", esp_err_to_name(errCode));
        return;
    }

    /* Set up Bluetooth Low Energy mode and enable controller */
    errCode = esp_bt_controller_enable(ESP_BT_MODE_BLE);
    if (errCode != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable Bluetooth Low Energy controller: %s", esp_err_to_name(errCode));
        return;
    }

    /* A queue for storing the received HCI packets */
    adv_queue = xQueueCreate(15, sizeof(hci_data_t));
    if (adv_queue == NULL) {
        ESP_LOGE(TAG, "Cannot create HCI IN queue");
        return;
    }

    /* Create the HCI buffer */
    hci_buffer = (uint8_t *)malloc(sizeof(uint8_t) * HCI_BUFFER_SIZE * HCI_EVENT_MAX_SIZE);
    if (hci_buffer == NULL) {
        ESP_LOGE(TAG, "Cannot allocate heap for HCI buffer.");
        return;
    }

    // Has to be set before any Bluetooth operations (like sending data, scanning or connecting).
    esp_vhci_host_register_callback(&vhci_host_cb);

    // Init Bluetooth procedures in a while loop with vTaskDelay to circumnavigate the watchdog timeouts
    bool ble_scan_initialising = true;
    int ble_scan_init_step = 0;
    while (ble_scan_initialising) {
        if (ble_scan_initialising && esp_vhci_host_check_send_available()) {
            uint16_t size;
            switch (ble_scan_init_step) {
                case 0:
                    ESP_LOGI(TAG, "Resetting Bluetooth controller");
                    size = make_cmd_reset(hci_message);
                    esp_vhci_host_send_packet(hci_message, size);
                    break;
                case 1:
                    ESP_LOGI(TAG, "Applying HCI event mask");
                    // Enable only LE Meta events => bit 61
                    uint8_t evt_mask[8] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20};
                    size = make_cmd_set_evt_mask(hci_message, evt_mask);
                    esp_vhci_host_send_packet(hci_message, size);
                    break;
                case 2:
                    ESP_LOGI(TAG, "Setting up BLE Scan parameters");
                    // Set up the passive scan
                    uint8_t scan_type = 0x00;

                    // Interval and Window are set in terms of number of slots (625 microseconds)
                    uint16_t scan_interval = 0x50;  // How often to scan
                    uint16_t scan_window = 0x50;    // How long to scan

                    uint8_t own_addr_type = 0x00;   // Public device address
                    uint8_t filter_policy = 0x00;   // Do not further filter any packets

                    size = make_cmd_ble_set_scan_params(hci_message, scan_type, scan_interval, scan_window, own_addr_type, filter_policy);
                    esp_vhci_host_send_packet(hci_message, size);
                    break;
                case 3:
                    ESP_LOGI(TAG, "Locking the BLE Scanning to channel %u", CHANNEL);
                    btdm_scan_channel_setting(CHANNEL);
                    esp_rom_printf("Locked to channel: %u\n", CHANNEL);
                    break;
                case 4: // Start the control thread
                    // FreeRTOS unrestricted task in ESP modification
                    // https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/freertos_idf.html
                    // Task stack size - 2048B - taken from ESP HCI example
                    // Priority 6 - taken from ESP HCI example
                    // Pinned to core 0 - taken from ESP HCI example
                    xTaskCreatePinnedToCore(&hci_evt_process, "Process HCI Event", 2048, NULL, 6, NULL, 0);
                    break;
                case 5: // Start BLE Scan
                    ESP_LOGI(TAG, "Starting BLE Scanning");
                    uint8_t scan_enable = 0x01;
                    uint8_t scan_filter_dups = 0x00;    // Disable duplicates filtering
                    size = make_cmd_ble_set_scan_enable(hci_message, scan_enable, scan_filter_dups);
                    esp_vhci_host_send_packet(hci_message, size);
                    ble_scan_initialising = false;
                    break;
                default:
                    ble_scan_initialising = false;
                    break;
            }
            ble_scan_init_step++;
        }
        vTaskDelay(1000 / portTICK_PERIOD_MS);  // Watchdog
    }
}
