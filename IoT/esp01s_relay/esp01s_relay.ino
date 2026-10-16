/*
 * ESP-01S relay node (ESP8266), driven by the ESP-NOW antenna bridge.
 *
 * The relay acts as a push button wired across a machine's power switch (Mercury):
 *   "on"      -> a short press              (PRESS_MS)
 *   "off"     -> a long press, forcing off  (HOLD_MS)
 *   "restart" -> a long press, a pause (RESTART_GAP_MS), then a short press
 *
 * Message from the antenna (ESP-NOW payload, JSON):  {"action": "on"}
 *   ("state" or "command" instead of "action", or a bare on/off/restart string, also work)
 * Replies to the sender, which the antenna passes back to the Pi:
 *   {"ok":true,"action":"off","status":"started","ms":6000}   as soon as it starts
 *   {"ok":true,"action":"off","status":"done"}                when the press sequence ends
 *   {"ok":false,"error":"..."}                                for a bad command
 *
 * Pairing: for the first 10 s after boot the MAC address is printed every half second
 * (Serial, 115200 baud). Put that MAC in the Mercury settings (MERCURY_MAC).
 *
 * Board: "Generic ESP8266 Module" (esp8266 core 3.x). Library: ArduinoJson 6 or 7.
 */

#include <ESP8266WiFi.h>
#include <espnow.h>
#include <ArduinoJson.h>

// ========== CONFIGURATION ==========
#define CHANNEL 1                 // must match the antenna (and every ESP-NOW device)
#define SERIAL_BAUD 115200
#define RELAY_PIN 0               // GPIO0 on the common ESP-01S relay board
#define RELAY_ACTIVE_LOW true     // most ESP-01S relay boards close the relay when GPIO0 is LOW

const unsigned long PRESS_MS = 500;          // "on": short press
const unsigned long HOLD_MS = 6000;          // "off": long press (most PCs force off after 4-5 s)
const unsigned long RESTART_GAP_MS = 3000;   // "restart": wait between the long and the short press
const unsigned long MAC_SPAM_MS = 10000;     // print the MAC for this long after boot
const unsigned long MAC_SPAM_EVERY_MS = 500;

// Only accept commands from this sender (the antenna's MAC). All zeros = accept anyone.
// ESP-NOW here is unencrypted, so set this once paired.
uint8_t ALLOWED_SENDER[6] = {0, 0, 0, 0, 0, 0};

// ========== STATE ==========
// The receive callback runs in the WiFi task: it only copies the message; loop() acts on it.
volatile bool rxReady = false;
char rxBuf[251];                  // ESP-NOW payloads are at most 250 bytes
uint8_t rxFrom[6];

void relayWrite(bool closed) {
  digitalWrite(RELAY_PIN, (closed != RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void press(unsigned long ms) {
  relayWrite(true);
  delay(ms);                      // delay() yields, so WiFi keeps running
  relayWrite(false);
}

String macToString(const uint8_t *mac) {
  char s[18];
  snprintf(s, sizeof(s), "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(s);
}

bool senderAllowed(const uint8_t *mac) {
  bool any = true;
  for (int i = 0; i < 6; i++) if (ALLOWED_SENDER[i]) any = false;
  return any || memcmp(mac, ALLOWED_SENDER, 6) == 0;
}

void reply(uint8_t *to, JsonDocument &doc) {
  char out[250];
  size_t n = serializeJson(doc, out, sizeof(out));
  if (!esp_now_is_peer_exist(to)) esp_now_add_peer(to, ESP_NOW_ROLE_COMBO, CHANNEL, NULL, 0);
  esp_now_send(to, (uint8_t *)out, n);
  Serial.print("[REPLY] ");
  Serial.println(out);
}

void onDataRecv(uint8_t *mac, uint8_t *data, uint8_t len) {
  if (rxReady) return;            // still handling the previous command; the sender gets no reply
  memcpy(rxFrom, mac, 6);
  memcpy(rxBuf, data, len);
  rxBuf[len] = '\0';
  rxReady = true;
}

void onDataSent(uint8_t *mac, uint8_t status) {
  Serial.printf("[ESP-NOW] reply to %s %s\n", macToString(mac).c_str(), status == 0 ? "OK" : "FAILED");
}

// Reads the action from {"action":...}, {"state":...}, {"command":...} or a bare string.
String readAction(const char *msg) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, msg) == DeserializationError::Ok) {
    if (doc.is<const char *>()) return String(doc.as<const char *>());
    for (const char *key : {"action", "state", "command"}) {
      if (doc[key].is<const char *>()) return String(doc[key].as<const char *>());
    }
    return "";
  }
  return String(msg);             // plain text such as: on
}

void handle(uint8_t *from, const char *msg) {
  Serial.printf("[CMD] from %s: %s\n", macToString(from).c_str(), msg);
  StaticJsonDocument<200> res;
  if (!senderAllowed(from)) {
    Serial.println("[CMD] ignored: sender not allowed");
    return;
  }
  String action = readAction(msg);
  action.trim();
  action.toLowerCase();

  unsigned long total;
  if (action == "on") total = PRESS_MS;
  else if (action == "off") total = HOLD_MS;
  else if (action == "restart") total = HOLD_MS + RESTART_GAP_MS + PRESS_MS;
  else {
    res["ok"] = false;
    res["error"] = "unknown action; use on, off or restart";
    reply(from, res);
    return;
  }

  res["ok"] = true;
  res["action"] = action;
  res["status"] = "started";
  res["ms"] = total;
  reply(from, res);

  if (action == "on") {
    press(PRESS_MS);
  } else if (action == "off") {
    press(HOLD_MS);
  } else {
    press(HOLD_MS);
    delay(RESTART_GAP_MS);
    press(PRESS_MS);
  }

  res.clear();
  res["ok"] = true;
  res["action"] = action;
  res["status"] = "done";
  reply(from, res);
}

void setup() {
  // Set the idle level before switching the pin to output, so the relay doesn't click at boot.
  relayWrite(false);
  pinMode(RELAY_PIN, OUTPUT);
  relayWrite(false);

  Serial.begin(SERIAL_BAUD);
  delay(200);
  Serial.println("\n=== ESP-01S relay node ===");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  wifi_set_channel(CHANNEL);
  if (esp_now_init() != 0) {
    Serial.println("[FATAL] ESP-NOW init failed; restarting");
    delay(1000);
    ESP.restart();
  }
  esp_now_set_self_role(ESP_NOW_ROLE_COMBO);
  esp_now_register_recv_cb(onDataRecv);
  esp_now_register_send_cb(onDataSent);
  Serial.println("[OK] Waiting for commands from the antenna");
}

void loop() {
  static unsigned long lastSpam = 0;
  unsigned long now = millis();
  if (now < MAC_SPAM_MS && now - lastSpam >= MAC_SPAM_EVERY_MS) {
    lastSpam = now;
    Serial.print("[PAIR] MAC: ");
    Serial.println(WiFi.macAddress());
  }

  if (rxReady) {
    char msg[251];
    uint8_t from[6];
    memcpy(msg, rxBuf, sizeof(msg));
    memcpy(from, rxFrom, 6);
    handle(from, msg);
    rxReady = false;              // accept the next command only after this one finished
  }
  delay(5);
}
