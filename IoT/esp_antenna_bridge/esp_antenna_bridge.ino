/*
 * ESP-NOW Antenna Bridge for XIAO ESP32-C3 (ESP32 board package 3.3.x)
 *
 * Plugged into the Pi by USB. The Pi (pi/espnow_serial_bridge.py) writes one JSON
 * command per line:
 *     {"request_id": "...", "target_mac": "AA:BB:CC:DD:EE:FF", "payload": {...}}
 * The payload is sent to the target over ESP-NOW. Every reply from a device comes
 * back to the Pi as one JSON line:
 *     {"request_id": "...", "type": "response", "from_mac": "...", "payload": "<device's text>"}
 * or, when sending failed:
 *     {"request_id": "...", "type": "error", "message": "..."}
 * Other lines (starting with '[' or '=') are human-readable logs; the Pi ignores them.
 *
 * Changes from the earlier version: the received data is copied with its real length
 * (it isn't NUL-terminated, so String((char*)data) could read past the end), peers are
 * added only once, sendSuccess is volatile and the send wait is longer, and the reply's
 * request_id is the one of the last command sent to that device.
 */

#include <esp_now.h>
#include <WiFi.h>
#include <ArduinoJson.h>

// ========== CONFIGURATION ==========
#define CHANNEL 1           // WiFi channel (must match all ESP-NOW devices)
#define SERIAL_BAUD 115200
#define MAX_RETRIES 3       // number of send retries before failure
#define SEND_WAIT_MS 100    // how long to wait for the send callback per attempt
#define MAX_PEERS 16        // request ids remembered per device

// ========== GLOBAL VARIABLES ==========
volatile bool sendDone = false;
volatile bool sendSuccess = false;
String incomingCommand = "";

// Last request id sent to each device, so replies are matched to the right request.
uint8_t peerMacs[MAX_PEERS][6];
String peerRequest[MAX_PEERS];
int peerCount = 0;

String macAddrToString(const uint8_t *mac) {
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(macStr);
}

bool stringToMac(const String &macStr, uint8_t *mac) {
  unsigned int v[6];
  if (sscanf(macStr.c_str(), "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6) return false;
  for (int i = 0; i < 6; i++) {
    if (v[i] > 0xFF) return false;
    mac[i] = (uint8_t)v[i];
  }
  return true;
}

int peerIndex(const uint8_t *mac) {
  for (int i = 0; i < peerCount; i++) if (memcmp(peerMacs[i], mac, 6) == 0) return i;
  return -1;
}

void rememberRequest(const uint8_t *mac, const String &requestId) {
  int i = peerIndex(mac);
  if (i < 0) {
    i = peerCount < MAX_PEERS ? peerCount++ : MAX_PEERS - 1;
    memcpy(peerMacs[i], mac, 6);
  }
  peerRequest[i] = requestId;
}

void emitError(const String &requestId, const char *message) {
  StaticJsonDocument<256> err;
  err["request_id"] = requestId;
  err["type"] = "error";
  err["message"] = message;
  serializeJson(err, Serial);
  Serial.println();
}

// ========== CALLBACKS (ESP32 package 3.3.x signatures) ==========
void OnDataSent(const wifi_tx_info_t *tx_info, esp_now_send_status_t status) {
  sendSuccess = (status == ESP_NOW_SEND_SUCCESS);
  sendDone = true;
}

void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
  const uint8_t *mac = info->src_addr;
  char text[251];
  int n = len < 250 ? len : 250;
  memcpy(text, incomingData, n);
  text[n] = '\0';

  int i = peerIndex(mac);
  StaticJsonDocument<512> doc;
  doc["request_id"] = i >= 0 ? peerRequest[i] : String("");
  doc["type"] = "response";
  doc["from_mac"] = macAddrToString(mac);
  doc["payload"] = text;
  serializeJson(doc, Serial);
  Serial.println();
}

// ========== ESP-NOW ==========
bool initESPNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.setChannel(CHANNEL);
  if (esp_now_init() != ESP_OK) {
    Serial.println("[ERROR] ESP-NOW init failed");
    return false;
  }
  esp_now_register_send_cb(OnDataSent);
  esp_now_register_recv_cb(OnDataRecv);
  return true;
}

bool addPeer(const uint8_t *mac) {
  if (esp_now_is_peer_exist(mac)) return true;
  esp_now_peer_info_t peerInfo;
  memset(&peerInfo, 0, sizeof(peerInfo));
  memcpy(peerInfo.peer_addr, mac, 6);
  peerInfo.channel = CHANNEL;
  peerInfo.encrypt = false;
  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("[ERROR] Failed to add peer");
    return false;
  }
  return true;
}

bool sendESPNow(const uint8_t *targetMac, const String &message) {
  if (message.length() > 250) return false;
  if (!addPeer(targetMac)) return false;
  for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    sendDone = false;
    sendSuccess = false;
    if (esp_now_send(targetMac, (const uint8_t *)message.c_str(), message.length()) == ESP_OK) {
      unsigned long start = millis();
      while (!sendDone && millis() - start < SEND_WAIT_MS) delay(1);
      if (sendSuccess) return true;
    }
    delay(100);
  }
  return false;
}

void handleCommand(const String &line) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, line)) {
    Serial.println("[ERROR] Invalid JSON from Pi");
    return;
  }
  String requestId = doc["request_id"] | "";
  String targetMac = doc["target_mac"] | "";
  String payload;
  serializeJson(doc["payload"], payload);

  uint8_t mac[6];
  if (!stringToMac(targetMac, mac)) {
    emitError(requestId, "Invalid MAC address format");
    return;
  }
  rememberRequest(mac, requestId);
  Serial.printf("[CMD] %s -> %s: %s\n", requestId.c_str(), targetMac.c_str(), payload.c_str());
  if (!sendESPNow(mac, payload)) emitError(requestId, "ESP-NOW send failed after retries");
}

// ========== ARDUINO ==========
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);
  Serial.println("\n=== ESP-NOW Antenna Bridge ===");
  Serial.print("[INFO] Chip: "); Serial.println(ESP.getChipModel());
  Serial.print("[INFO] MAC: "); Serial.println(WiFi.macAddress());
  if (!initESPNow()) {
    Serial.println("[FATAL] Cannot initialize ESP-NOW");
    while (1) delay(100);
  }
  Serial.println("[OK] Ready - waiting for commands from the Pi");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      incomingCommand.trim();
      if (incomingCommand.length() > 0) handleCommand(incomingCommand);
      incomingCommand = "";
    } else if (incomingCommand.length() < 1024) {
      incomingCommand += c;
    }
  }
  delay(5);
}
