#include <bluefruit.h>

#define LED_PIN 6  // LED is verbonden met pin D6
#define BUZZER_PIN1 7  // Eerste pin voor buzzer (D7)
#define BUZZER_PIN2 8  // Tweede pin voor buzzer (D8)

// UUID's voor de UART-service en -karakteristieken
#define UART_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define UART_RX_CHARACTERISTIC_UUID "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define UART_TX_CHARACTERISTIC_UUID "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

// BLE UART-service en -karakteristieken
BLEService uartService = BLEService(UART_SERVICE_UUID);
BLECharacteristic uartRxCharacteristic = BLECharacteristic(UART_RX_CHARACTERISTIC_UUID);
BLECharacteristic uartTxCharacteristic = BLECharacteristic(UART_TX_CHARACTERISTIC_UUID);

// Variabelen voor het knipperen
bool isBlinking = false;  // Geeft aan of de LED en buzzer moeten knipperen
unsigned long previousMillis = 0;  // Tijdstip van de laatste toestandswisseling
const long blinkInterval = 500;  // Interval voor knipperen (500 ms)

void setup() {
    Serial.begin(115200);
    // Verwijder of conditioneer de volgende regel:
    // while (!Serial);  // Wacht niet op seriële verbinding

    pinMode(LED_PIN, OUTPUT);  // Zet pin D6 in als output
    digitalWrite(LED_PIN, LOW);  // Zorg ervoor dat de LED uit is bij start
    pinMode(BUZZER_PIN1, OUTPUT);  // Zet pin D7 in als output
    pinMode(BUZZER_PIN2, OUTPUT);  // Zet pin D8 in als output
    digitalWrite(BUZZER_PIN1, LOW);  // Zorg ervoor dat de buzzer uit is bij start
    digitalWrite(BUZZER_PIN2, LOW);  // Zorg ervoor dat de buzzer uit is bij start

    // Initialiseer Bluetooth
    Bluefruit.begin();
    Bluefruit.setName("XIAO_Central");
    Bluefruit.setTxPower(4);

    // Configureer de UART-service
    setupUART();

    // Start het adverteren
    startAdv();

    Serial.println("Bluetooth klaar!");
}

void setupUART() {
    // Configureer de UART-service
    uartService.begin();

    // Configureer de RX-karakteristiek
    uartRxCharacteristic.setProperties(CHR_PROPS_WRITE);
    uartRxCharacteristic.setPermission(SECMODE_OPEN, SECMODE_OPEN);
    uartRxCharacteristic.setFixedLen(20);
    uartRxCharacteristic.begin();
    uartRxCharacteristic.setWriteCallback(uartRxCallback);

    // Configureer de TX-karakteristiek
    uartTxCharacteristic.setProperties(CHR_PROPS_NOTIFY);
    uartTxCharacteristic.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
    uartTxCharacteristic.setFixedLen(20);
    uartTxCharacteristic.begin();

    // Start de UART-service
    Bluefruit.Advertising.addService(uartService);
}

void uartRxCallback(uint16_t conn_handle, BLECharacteristic* chr, uint8_t* data, uint16_t len) {
    // Verwerk ontvangen data
    if (len > 0) {
        char received = data[0];  // Lees het eerste karakter
        if (received == '2') {
            // Start het knipperen
            isBlinking = true;
            Serial.println("LED en Buzzer KNIPPEREN");
        } else if (received == '0') {
            // Stop het knipperen en zet de LED en buzzer uit
            isBlinking = false;
            digitalWrite(LED_PIN, LOW);
            digitalWrite(BUZZER_PIN1, LOW);
            digitalWrite(BUZZER_PIN2, LOW);
            Serial.println("LED en Buzzer UIT");
        }
    }
}

void startAdv() {
    // Start het adverteren
    Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
    Bluefruit.Advertising.addTxPower();
    Bluefruit.Advertising.addService(uartService);
    Bluefruit.Advertising.start(0);  // Adverteren zonder tijdslimiet
}

void loop() {
    // Beheer het knipperen van de LED en buzzer
    if (isBlinking) {
        unsigned long currentMillis = millis();
        if (currentMillis - previousMillis >= blinkInterval) {
            // Wissel de toestand van de LED en buzzer
            digitalWrite(LED_PIN, !digitalRead(LED_PIN));
            digitalWrite(BUZZER_PIN1, !digitalRead(BUZZER_PIN1));
            digitalWrite(BUZZER_PIN2, !digitalRead(BUZZER_PIN2));
            previousMillis = currentMillis;
        }
    }
}