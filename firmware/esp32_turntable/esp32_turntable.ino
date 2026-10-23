// Protocol used by Scanner Workstation:
//   app -> ESP32: ROTATE\n
//   ESP32 -> app: OK\n (only after the complete revolution)
// A full rotation is detected by an index/reference sensor, not by assuming
// a motor step count. Place one marker/magnet on the turntable and connect
// its sensor to INDEX_PIN. The sensor must be LOW at the reference position.
const int STEP_PIN = 26;
const int DIR_PIN = 27;
const int ENABLE_PIN = 25; // Set to -1 if ENABLE is not wired.
const int INDEX_PIN = 33;
const int STEP_DELAY_US = 700;

void setup() {
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(INDEX_PIN, INPUT_PULLUP);
  if (ENABLE_PIN >= 0) pinMode(ENABLE_PIN, OUTPUT);
  if (ENABLE_PIN >= 0) digitalWrite(ENABLE_PIN, LOW); // most drivers: LOW = enabled
  digitalWrite(DIR_PIN, HIGH);
  Serial.begin(115200);
  Serial.println("READY");
}

void loop() {
  if (!Serial.available()) return;
  String command = Serial.readStringUntil('\n');
  command.trim();
  if (command.equalsIgnoreCase("ROTATE")) {
    // Move away from the current marker first, so a command issued while the
    // marker is already under the sensor still produces a complete rotation.
    while (digitalRead(INDEX_PIN) == LOW) stepMotor();

    // Continue until the marker returns. No steps-per-revolution assumption.
    unsigned long started = millis();
    while (digitalRead(INDEX_PIN) == HIGH) {
      stepMotor();
      // Safety timeout for a disconnected sensor or jammed turntable.
      if (millis() - started > 120000UL) {
        Serial.println("ERR INDEX_TIMEOUT");
        return;
      }
    }
    while (digitalRead(INDEX_PIN) == LOW) stepMotor();
    Serial.println("OK");
  } else if (command.length()) {
    Serial.println("ERR UNKNOWN_COMMAND");
  }
}

void stepMotor() {
      digitalWrite(STEP_PIN, HIGH);
      delayMicroseconds(STEP_DELAY_US);
      digitalWrite(STEP_PIN, LOW);
      delayMicroseconds(STEP_DELAY_US);
}
