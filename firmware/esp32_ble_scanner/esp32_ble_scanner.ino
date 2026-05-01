/*
 * esp32_ble_scanner.ino
 * Passive BLE advertisement collector for the warDrive project.
 *
 * Outputs one NDJSON line per advertisement to USB serial.
 * Timestamps are millis()-since-boot; the host adds wall-clock UTC.
 *
 * Passive scan only — no scan requests, no connections, no TX.
 *
 * Tested with:
 *   Board:   ESP32 Dev Module (original ESP32 / ESP32-S3)
 *   Library: ESP32 BLE Arduino (bundled with esp32 Arduino core)
 *
 * NOT compatible with ESP32-S2 (no Bluetooth hardware).
 * Run `esptool.py chip_id` to confirm your chip before flashing.
 */

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

// ── Configuration ─────────────────────────────────────────────────────────────
// Scan window / interval in units of 0.625 ms.
// 0x0064 = 100 ms → window=99 ms / interval=100 ms (≈99% duty cycle).
// These raw values are passed directly to the BLE controller.
static const uint16_t SCAN_INTERVAL_UNITS = 0x0064;  // 100 ms
static const uint16_t SCAN_WINDOW_UNITS   = 0x0063;  //  99 ms

// Serial baud rate. 921600 if you observe drops in dense environments.
static const uint32_t SERIAL_BAUD = 115200;

// BLE scan duration (0 = continuous). We restart the scan in the loop
// rather than using a long duration so the callback stays live.
static const uint32_t SCAN_DURATION_SEC = 5;

// ── Apple Continuity type decoder ─────────────────────────────────────────────
// Only the leading type byte of the Continuity payload is decoded.
// Ref: https://github.com/furiousMAC/continuity (public research)
static const char* appleContinuityType(uint8_t typeByte) {
    switch (typeByte) {
        case 0x00: return "Handoff";
        case 0x05: return "AirDrop";
        case 0x07: return "AirPods";
        case 0x08: return "Siri";
        case 0x09: return "AirPlay_Dest";
        case 0x0A: return "AirPlay_Src";
        case 0x0B: return "MagicSwitch";
        case 0x0C: return "Continuity";
        case 0x0D: return "Watch";
        case 0x0F: return "NearbyAction";
        case 0x10: return "NearbyInfo";
        case 0x12: return "FindMy";
        case 0x13: return "iPhone";
        case 0x14: return "iPad";
        case 0x1C: return "Mac";
        case 0x1E: return "HomePod";
        default:   return nullptr;
    }
}

// ── JSON helpers ──────────────────────────────────────────────────────────────
// Escape a string for JSON (handles quotes and backslashes).
static void jsonEscapeString(const String& s, String& out) {
    out = "\"";
    for (char c : s) {
        if (c == '"')       out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c < 0x20)  { /* skip control chars */ }
        else                out += c;
    }
    out += "\"";
}

// Hex-encode a byte buffer into a String.
static String hexEncode(const uint8_t* data, size_t len) {
    String out;
    out.reserve(len * 2);
    static const char hex[] = "0123456789ABCDEF";
    for (size_t i = 0; i < len; i++) {
        out += hex[(data[i] >> 4) & 0xF];
        out += hex[data[i] & 0xF];
    }
    return out;
}

// ── BLE Scan callback ──────────────────────────────────────────────────────────
class AdvertCallback : public BLEAdvertisedDeviceCallbacks {
public:
    void onResult(BLEAdvertisedDevice dev) override {
        String line;
        line.reserve(256);

        // Timestamp: millis since boot. Host side replaces with UTC wall clock.
        line += "{\"ms\":";
        line += String(millis());

        // BD_ADDR in uppercase colon-separated form
        String addr = dev.getAddress().toString().c_str();
        addr.toUpperCase();
        String escapedAddr;
        jsonEscapeString(addr, escapedAddr);
        line += ",\"addr\":";
        line += escapedAddr;

        // Address type:
        // 0 = public, 1 = random
        // BLE spec further sub-types randoms by the two high bits of the address,
        // but the Arduino library only exposes public vs random. We preserve that.
        esp_ble_addr_type_t addrType = dev.getAddressType();
        line += ",\"addr_type\":";
        line += String((int)addrType);

        // Locally-administered (randomized) bit — bit 1 of the high byte
        // When set, this address is not a globally-unique OUI assignment.
        uint8_t highByte = 0;
        {
            const std::string rawAddr = dev.getAddress().toString();
            // Parse first octet (MSB in the printed form)
            highByte = (uint8_t)strtol(rawAddr.c_str(), nullptr, 16);
        }
        bool isRandom = (highByte & 0x02) != 0;
        line += ",\"rand\":";
        line += isRandom ? "1" : "0";

        // RSSI
        line += ",\"rssi\":";
        line += String(dev.getRSSI());

        // Advertised name (may be absent)
        if (dev.haveName()) {
            String escapedName;
            jsonEscapeString(String(dev.getName().c_str()), escapedName);
            line += ",\"name\":";
            line += escapedName;
        }

        // TX Power (may be absent)
        if (dev.haveTXPower()) {
            line += ",\"tx_power\":";
            line += String(dev.getTXPower());
        }

        // Appearance (may be absent)
        if (dev.haveAppearance()) {
            line += ",\"appearance\":";
            line += String(dev.getAppearance());
        }

        // Manufacturer data
        bool hasAppleContinuity = false;
        const char* continuityType = nullptr;
        if (dev.haveManufacturerData()) {
            std::string mfgRaw = dev.getManufacturerData();
            if (mfgRaw.size() >= 2) {
                // Little-endian manufacturer ID
                uint16_t mfgId = (uint8_t)mfgRaw[0] | ((uint8_t)mfgRaw[1] << 8);
                line += ",\"mfg_id\":";
                line += String(mfgId);
                line += ",\"mfg_hex\":\"";
                line += hexEncode((const uint8_t*)mfgRaw.data(), mfgRaw.size());
                line += "\"";

                // Apple Continuity (manufacturer ID 0x004C)
                if (mfgId == 0x004C && mfgRaw.size() >= 3) {
                    uint8_t contType = (uint8_t)mfgRaw[2];
                    continuityType = appleContinuityType(contType);
                    if (continuityType) {
                        hasAppleContinuity = true;
                    }
                }
            }
        }

        if (hasAppleContinuity) {
            String escapedCT;
            jsonEscapeString(String(continuityType), escapedCT);
            line += ",\"apple_type\":";
            line += escapedCT;
        }

        // Service UUIDs
        if (dev.haveServiceUUID()) {
            line += ",\"services\":[";
            int count = dev.getServiceUUIDCount();
            for (int i = 0; i < count; i++) {
                if (i > 0) line += ",";
                String uuid = dev.getServiceUUID(i).toString().c_str();
                uuid.toUpperCase();
                String escapedUUID;
                jsonEscapeString(uuid, escapedUUID);
                line += escapedUUID;
            }
            line += "]";
        }

        // Raw advertisement payload (hex). This preserves everything for
        // future offline parsing without bloating the structured fields.
        std::string rawPayload = dev.getPayload() ?
            std::string((char*)dev.getPayload(), dev.getPayloadLength()) : "";
        if (!rawPayload.empty()) {
            line += ",\"raw\":\"";
            line += hexEncode((const uint8_t*)rawPayload.data(), rawPayload.size());
            line += "\"";
        }

        line += "}";

        // Emit — Serial.println adds \n
        Serial.println(line);
    }
};

// ── Setup ─────────────────────────────────────────────────────────────────────
BLEScan* bleScan = nullptr;
AdvertCallback advCallback;

void setup() {
    Serial.begin(SERIAL_BAUD);
    // Wait for serial to stabilize (important for USB CDC on some boards)
    delay(500);

    // Emit a startup banner so the host can detect the device is ready.
    // The host reader skips lines that don't start with '{'.
    Serial.println("# esp32_ble_scanner ready");

    BLEDevice::init("");  // empty name — we don't advertise anything

    bleScan = BLEDevice::getScan();
    bleScan->setAdvertisedDeviceCallbacks(&advCallback, /*wantDuplicates=*/true);

    // PASSIVE scan: the controller listens only; no scan-request PDUs are sent.
    bleScan->setActiveScan(false);

    // High duty-cycle scan parameters (raw 0.625 ms units)
    bleScan->setInterval(SCAN_INTERVAL_UNITS);
    bleScan->setWindow(SCAN_WINDOW_UNITS);
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
    // Start a bounded scan; results arrive via callback during this call.
    // When it completes, we immediately restart so coverage is continuous.
    bleScan->start(SCAN_DURATION_SEC, /*async=*/false);
    bleScan->clearResults();  // free memory before next cycle
}
