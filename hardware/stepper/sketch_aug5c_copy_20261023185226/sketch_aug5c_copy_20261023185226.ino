
const int IN1 = 19;
const int IN2 = 18;
const int IN3 = 5;
const int IN4 = 17;

const int STEP_DELAY_MS = 2;
const long STEPS_PER_REVOLUTION = 4096;
const bool CLOCKWISE = true;

const int sequence[8][4] = {
  {1, 0, 0, 0},
  {1, 1, 0, 0},
  {0, 1, 0, 0},
  {0, 1, 1, 0},
  {0, 0, 1, 0},
  {0, 0, 1, 1},
  {0, 0, 0, 1},
  {1, 0, 0, 1}
};

int stepIndex = 0;

void setCoils(int s) {
  digitalWrite(IN1, sequence[s][0]);
  digitalWrite(IN2, sequence[s][1]);
  digitalWrite(IN3, sequence[s][2]);
  digitalWrite(IN4, sequence[s][3]);
}

void releaseCoils() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void rotateOneRevolution() {
  Serial.println("BUSY");

  for (long i = 0; i < STEPS_PER_REVOLUTION; i++) {
    setCoils(stepIndex);
    delay(STEP_DELAY_MS);

    if (CLOCKWISE) {
      stepIndex = (stepIndex + 1) % 8;
    } else {
      stepIndex = (stepIndex + 7) % 8;
    }
  }

  releaseCoils();
  Serial.println("OK");
}

void setup() {
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  Serial.begin(115200);
  releaseCoils();

  Serial.println("READY");
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    if (command == "ROTATE") {
      rotateOneRevolution();
    } else if (command == "PING") {
      Serial.println("OK");
    } else {
      Serial.println("ERROR: UNKNOWN COMMAND");
    }
  }
}
