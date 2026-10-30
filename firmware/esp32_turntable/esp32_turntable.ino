#include <BleKeyboard.h>

const int IN1 = 19, IN2 = 18, IN3 = 5, IN4 = 17;
const int PHOTOS_PER_REV = 30;
const int STEP_DELAY_MS = 3;
const int SETTLE_MS = 1500;
const int AFTER_SHOT_MS = 2000;
const int STEPS_PER_REV = 4096;

BleKeyboard bleKeyboard("Turntable", "DIY", 100);
const int sequence[8][4] = {
  {1,0,0,0}, {1,1,0,0}, {0,1,0,0}, {0,1,1,0},
  {0,0,1,0}, {0,0,1,1}, {0,0,0,1}, {1,0,0,1}
};
int seqIndex = 0;

void setCoils(int s) {
  digitalWrite(IN1, sequence[s][0]); digitalWrite(IN2, sequence[s][1]);
  digitalWrite(IN3, sequence[s][2]); digitalWrite(IN4, sequence[s][3]);
}
void releaseCoils() {
  digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW); digitalWrite(IN4, LOW);
}
void stepMotor(int steps) {
  for (int i = 0; i < steps; i++) {
    setCoils(seqIndex); seqIndex = (seqIndex + 1) % 8; delay(STEP_DELAY_MS);
  }
  releaseCoils();
}
void takePhoto() { bleKeyboard.write(KEY_MEDIA_VOLUME_UP); }

void setup() {
  Serial.begin(115200);
  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
  bleKeyboard.begin();
  Serial.println("Waiting for phone to connect (pair with 'Turntable')...");
}
void loop() {
  if (!bleKeyboard.isConnected()) { delay(500); return; }
  Serial.println("Connected. Starting scan in 5 seconds..."); delay(5000);
  long stepsDone = 0;
  for (int photo = 1; photo <= PHOTOS_PER_REV; photo++) {
    delay(SETTLE_MS); takePhoto();
    Serial.print("Photo "); Serial.print(photo);
    Serial.print(" / "); Serial.println(PHOTOS_PER_REV); delay(AFTER_SHOT_MS);
    long target = (long)photo * STEPS_PER_REV / PHOTOS_PER_REV;
    stepMotor(target - stepsDone); stepsDone = target;
  }
  Serial.println("Scan complete.");
  while (true) delay(1000);
}
