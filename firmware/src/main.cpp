#include <Arduino.h>
#include <ctype.h>
#define DECODE_NEC
#define NO_LED_FEEDBACK_CODE
#include <IRremote.hpp>
#include <stdlib.h>
#include <string.h>

const int ELECTRIC_MOTOR_PIN = 5;
const int BATTERY_VOLTAGE_PIN = A0;
const int MOTOR_OUTPUT_VOLTAGE_PIN = A1;
const double ADC_REFERENCE_VOLTAGE = 5.148;
const double VOLTAGE_DIVIDER_SCALE = 3.0;
int batteryVoltageValue = 0;
int motorOutputVoltageValue = 0;
double batteryVoltage = 0;
double motorOutputVoltage = 0;
const int COMBUSTION_ENGINE_PIN = 7;
const int ENCODER_PIN = 2;
const int IR_RECEIVE_PIN = 3;
const double PULSES_PER_REV = 57.2;
const unsigned long SAMPLE_MS = 1000;
const size_t SERIAL_LINE_MAX = 96;
const bool ELECTRIC_MOTOR_PWM_ACTIVE_LOW = false;
const bool COMBUSTION_ENGINE_PWM_ACTIVE_LOW = false;
const uint16_t IR_COMMAND_POWER = 0x45;
const uint16_t IR_COMMAND_NUMBER_1 = 0x0C;
const uint16_t IR_COMMAND_NUMBER_2 = 0x18;
const uint16_t IR_COMMAND_NUMBER_3 = 0x5E;
const uint16_t IR_COMMAND_NUMBER_4 = 0x08;
const uint16_t IR_COMMAND_NUMBER_5 = 0x1C;
const uint16_t IR_COMMAND_NUMBER_6 = 0x5A;
const uint16_t IR_COMMAND_NUMBER_7 = 0x42;
const uint16_t IR_COMMAND_NUMBER_8 = 0x52;
const uint16_t IR_COMMAND_NUMBER_9 = 0x4A;

volatile long pulses = 0;
unsigned long lastRPMTime = 0;

int electricMotorPwm = 0;
int combustionEnginePwm = 0;
int electricMotorStage = 0;
double rpm = 0;

char serialLine[SERIAL_LINE_MAX];
size_t serialLineLength = 0;
bool serialLineOverflow = false;

void countPulses() {
    pulses++;
}

double dividerVoltageFromAdc(int adcValue) {
    return adcValue * (ADC_REFERENCE_VOLTAGE / 1023.0) * VOLTAGE_DIVIDER_SCALE;
}

void writeMotorPwm(int pin, int pwm, bool activeLow) {
    pwm = constrain(pwm, 0, 255);
    int outputPwm = activeLow ? 255 - pwm : pwm;

    if (outputPwm == 0) {
        analogWrite(pin, 0);
        digitalWrite(pin, LOW);
        return;
    }

    if (outputPwm == 255) {
        analogWrite(pin, 255);
        digitalWrite(pin, HIGH);
        return;
    }

    analogWrite(pin, outputPwm);
}

void applyMotorPwm() {
    writeMotorPwm(ELECTRIC_MOTOR_PIN, electricMotorPwm, ELECTRIC_MOTOR_PWM_ACTIVE_LOW);
    writeMotorPwm(COMBUSTION_ENGINE_PIN, combustionEnginePwm, COMBUSTION_ENGINE_PWM_ACTIVE_LOW);
}

bool parsePwmValue(const char *text, int &value) {
    if (text == nullptr || *text == '\0') {
        return false;
    }

    char *end = nullptr;
    long parsed = strtol(text, &end, 10);

    if (end == text || *end != '\0') {
        return false;
    }

    value = constrain(parsed, 0, 255);
    return true;
}

bool isCombustionEnginePwmKey(const char *key) {
    return strcmp(key, "ice_pwm") == 0 ||
           strcmp(key, "ICE_pwm") == 0 ||
           strcmp(key, "combustion_engine_pwm") == 0 ||
           strcmp(key, "internal_combustion_engine_pwm") == 0;
}

bool parseKeyValue(char *pair, int &parsedElectricMotorPwm, int &parsedCombustionEnginePwm) {
    char *equals = strchr(pair, '=');

    if (equals == nullptr) {
        return false;
    }

    *equals = '\0';
    const char *key = pair;
    const char *rawValue = equals + 1;
    int value = 0;

    if (!parsePwmValue(rawValue, value)) {
        return false;
    }

    if (strcmp(key, "electric_motor_pwm") == 0) {
        parsedElectricMotorPwm = value;
        return true;
    }

    if (isCombustionEnginePwmKey(key)) {
        parsedCombustionEnginePwm = value;
        return true;
    }

    return false;
}

void processSerialLine(char *line) {
    if (line[0] == '\0') {
        return;
    }

    int parsedElectricMotorPwm = electricMotorPwm;
    int parsedCombustionEnginePwm = combustionEnginePwm;
    bool hasValidCommand = false;

    char *pair = strtok(line, ",");

    while (pair != nullptr) {
        if (parseKeyValue(pair, parsedElectricMotorPwm, parsedCombustionEnginePwm)) {
            hasValidCommand = true;
        }

        pair = strtok(nullptr, ",");
    }

    if (!hasValidCommand) {
        return;
    }

    bool commandChanged = parsedElectricMotorPwm != electricMotorPwm ||
                          parsedCombustionEnginePwm != combustionEnginePwm;

    electricMotorPwm = parsedElectricMotorPwm;
    combustionEnginePwm = parsedCombustionEnginePwm;

    applyMotorPwm();

    if (commandChanged) {
        Serial.print("ok electric_motor_pwm=");
        Serial.print(electricMotorPwm);
        Serial.print(" internal_combustion_engine_pwm=");
        Serial.println(combustionEnginePwm);
    }
}

void readSerialCommands() {
    while (Serial.available() > 0) {
        char incoming = Serial.read();

        if (incoming == '\n') {
            if (!serialLineOverflow) {
                serialLine[serialLineLength] = '\0';
                processSerialLine(serialLine);
            }

            serialLineLength = 0;
            serialLineOverflow = false;
            continue;
        }

        if (incoming == '\r' || isspace((unsigned char)incoming)) {
            continue;
        }

        if (serialLineLength < SERIAL_LINE_MAX - 1) {
            serialLine[serialLineLength++] = incoming;
        } else {
            serialLineOverflow = true;
        }
    }
}

int getElectricMotorPwmForStage(int stage) {
    switch (stage) {
        case 1:
            return 28;
        case 2:
            return 57;
        case 3:
            return 85;
        case 4:
            return 113;
        case 5:
            return 142;
        case 6:
            return 170;
        case 7:
            return 198;
        case 8:
            return 227;
        case 9:
            return 255;
        default:
            return 0;
    }
}

void setElectricMotorStageFromRemote(int stage) {
    electricMotorStage = constrain(stage, 0, 9);
    electricMotorPwm = getElectricMotorPwmForStage(electricMotorStage);
    writeMotorPwm(ELECTRIC_MOTOR_PIN, electricMotorPwm, ELECTRIC_MOTOR_PWM_ACTIVE_LOW);

    Serial.print("ir electric_motor_stage=");
    Serial.print(electricMotorStage);
    Serial.print(" ");
    Serial.print("ir electric_motor_pwm=");
    Serial.println(electricMotorPwm);
}

void stopElectricMotorFromRemote() {
    setElectricMotorStageFromRemote(0);
}

bool getElectricMotorStageForNumberCommand(uint16_t command, int &stage) {
    switch (command) {
        case IR_COMMAND_NUMBER_1:
            stage = 1;
            return true;
        case IR_COMMAND_NUMBER_2:
            stage = 2;
            return true;
        case IR_COMMAND_NUMBER_3:
            stage = 3;
            return true;
        case IR_COMMAND_NUMBER_4:
            stage = 4;
            return true;
        case IR_COMMAND_NUMBER_5:
            stage = 5;
            return true;
        case IR_COMMAND_NUMBER_6:
            stage = 6;
            return true;
        case IR_COMMAND_NUMBER_7:
            stage = 7;
            return true;
        case IR_COMMAND_NUMBER_8:
            stage = 8;
            return true;
        case IR_COMMAND_NUMBER_9:
            stage = 9;
            return true;
        default:
            return false;
    }
}

void changeElectricMotorStageFromRemote(int delta) {
    setElectricMotorStageFromRemote(electricMotorStage + delta);
}

bool isVolumeUpCommand(uint16_t command) {
    return command == 0x02 || command == 0x15 || command == 0x40 || command == 0x46 || command == 0x47;
}

bool isVolumeDownCommand(uint16_t command) {
    return command == 0x07 || command == 0x19 || command == 0x98;
}

void readIrRemote() {
    if (!IrReceiver.decode()) {
        return;
    }

    uint16_t command = IrReceiver.decodedIRData.command;

    Serial.print("ir command=0x");
    Serial.println(command, HEX);

    int numberStage = 0;

    if (getElectricMotorStageForNumberCommand(command, numberStage)) {
        Serial.println("ir action=number_stage");
        setElectricMotorStageFromRemote(numberStage);
    } else if (isVolumeUpCommand(command)) {
        Serial.println("ir action=volume_up");
        changeElectricMotorStageFromRemote(1);
    } else if (isVolumeDownCommand(command)) {
        Serial.println("ir action=volume_down");
        changeElectricMotorStageFromRemote(-1);
    } else if (command == IR_COMMAND_POWER) {
        Serial.println("ir action=power");
        stopElectricMotorFromRemote();
    }

    IrReceiver.resume();
}

void setup() {
    Serial.begin(115200);

    pinMode(ELECTRIC_MOTOR_PIN, OUTPUT);
    pinMode(BATTERY_VOLTAGE_PIN, INPUT);
    pinMode(MOTOR_OUTPUT_VOLTAGE_PIN, INPUT);
    pinMode(COMBUSTION_ENGINE_PIN, OUTPUT);
    pinMode(ENCODER_PIN, INPUT_PULLUP);

    attachInterrupt(digitalPinToInterrupt(ENCODER_PIN), countPulses, RISING);
    IrReceiver.begin(IR_RECEIVE_PIN, DISABLE_LED_FEEDBACK);

    applyMotorPwm();

    lastRPMTime = millis();
}

void loop() {
    readSerialCommands();
    readIrRemote();

    batteryVoltageValue = analogRead(BATTERY_VOLTAGE_PIN);
    motorOutputVoltageValue = analogRead(MOTOR_OUTPUT_VOLTAGE_PIN);
    batteryVoltage = dividerVoltageFromAdc(batteryVoltageValue);
    motorOutputVoltage = dividerVoltageFromAdc(motorOutputVoltageValue);

    // RPM calculation every 1000 ms
    unsigned long now = millis();

    if (now - lastRPMTime >= SAMPLE_MS) {
        noInterrupts();
        long pulseCount = pulses;
        pulses = 0;
        interrupts();

        double revs = pulseCount / PULSES_PER_REV;
        rpm = revs * (60000.0 / SAMPLE_MS);

        Serial.print("telemetry rpm=");
        Serial.print(rpm);
        Serial.print(" battery_voltage_value=");
        Serial.print(batteryVoltageValue);
        Serial.print(" battery_voltage=");
        Serial.print(batteryVoltage);
        Serial.print(" motor_output_voltage_value=");
        Serial.print(motorOutputVoltageValue);
        Serial.print(" motor_output_voltage=");
        Serial.println(motorOutputVoltage);

        lastRPMTime = now;
    }
}
