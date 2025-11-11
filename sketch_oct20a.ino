#include "BluetoothSerial.h"
#include "HX711.h"
 
#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error Bluetooth er ikke aktivert for denne ESP32-byggen!
#endif
 
 
// HX711 circuit wiring
const int LOADCELL_DOUT_PIN = 34;
const int LOADCELL_SCK_PIN = 32;
 
HX711 scale;
BluetoothSerial SerialBT;
 
// Konfig
const char* DEVICE_NAME = "ESP32_GRUPPE4_02";
const uint8_t PACKET_TYPE_TELEMETRY = 0x01;  // 0x01 = "vekt-pakke"
 
// ---------- Pakkebygger ----------
void sendPacket(uint8_t type, const uint8_t* payload, uint16_t length) {
  // Header
  const uint8_t header[2] = {0xAA, 0x55};
  SerialBT.write(header, 2);
 
  // TYPE
  SerialBT.write(type);
 
  // LEN (big-endian)
  const uint8_t lenH = (length >> 8) & 0xFF;
  const uint8_t lenL = length & 0xFF;
  SerialBT.write(lenH);
  SerialBT.write(lenL);
 
  // PAYLOAD
  if (length > 0 && payload != nullptr) {
    SerialBT.write(payload, length);
  }
 
  // CHK (XOR av TYPE + LEN_H + LEN_L + PAYLOAD)
  uint8_t chk = type ^ lenH ^ lenL;
  for (uint16_t i = 0; i < length; i++) {
    chk ^= payload[i];
  }
  SerialBT.write(chk);
  SerialBT.flush();
}
 
// ---------- Payload: teller + VEKT I GRAM ----------
struct __attribute__((packed)) Telemetry {
  uint32_t counter;  // øker for hver sending
  int32_t  weight_g; // vekt i hele gram (negativt mulig om sensor er tare/under 0)
};
 
uint32_t counter = 0;
 
void setup() {
  Serial.begin(115200);
  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  while (!(scale.is_ready())){
    Serial.println("Waiting for scale");
    delay(100);
  }
  scale.set_scale(13593.41/345*1.24); // kalibreringsfactor
  scale.tare();
 
  delay(300);
 
  // Start SPP-server (ESP32 er synlig som DEVICE_NAME)
  if (!SerialBT.begin(DEVICE_NAME)) {
    Serial.println("Kunne ikke starte Bluetooth SPP.");
    while (true) { delay(1000); }
  }
 
  Serial.println("Bluetooth SPP klart.");
  // Tilgjengelig i nyere ESP32-cores:
  #if defined(ESP_ARDUINO_VERSION)
  Serial.printf("Enhetsnavn: %s\n", DEVICE_NAME);
  #endif
 
  // Hvis biblioteket ditt har denne:
  #ifdef ESP32
  // I noen varianter heter den getBtAddressString()
  #if __has_include("BluetoothSerial.h")
  Serial.printf("ESP32 Bluetooth-adresse: %s\n", SerialBT.getBtAddressString().c_str());
  #endif
  #endif
}
 
void loop() {
  // >>> BYTT UT DETTE MED EKTE SENSORVERDI <<<
  // Simuler en vekt som varierer rundt 3500 g (3.5 kg)
  long weightG = scale.get_units(50);
  //Serial.print("Result: ");
  //Serial.println(reading);
  //sendToRaspberry(reading);
  //delay(1000);
 
 
 
  // sin() gir -1..+1 -> skaler til ±200 g
  //float sim = 3500.0f + 200.0f * sin(millis() / 2000.0f);
  int32_t weight_g = (int32_t) roundf(weightG);
 
  Telemetry t{};
  t.counter  = counter++;
  t.weight_g = weight_g;
 
  // Send som payload
  sendPacket(PACKET_TYPE_TELEMETRY, (const uint8_t*)&t, sizeof(Telemetry));
 
  // (Valgfritt) status på USB-seriell
  Serial.printf("Sendte vekt: n=%lu, weight=%ld g\n",
                (unsigned long)t.counter, (long)t.weight_g);
 
  delay(0); // send hvert sekund
}
 
 