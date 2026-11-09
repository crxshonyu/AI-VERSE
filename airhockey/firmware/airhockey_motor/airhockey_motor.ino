/*
 * OPTIONAL REFERENCE: Arduino Uno + external STEP/DIR driver.
 * Protocol v1 matches airhockey/motor.py. This has NOT been run on hardware.
 * Install AccelStepper using the Arduino Library Manager (official library).
 *
 * NC switches: pin -> normally-closed contact -> GND; INPUT_PULLUP.
 * LOW = circuit intact / released; HIGH = hit, emergency stop, or broken wire.
 * STEP=2 DIR=3 ENABLE_N=4 LEFT_NC=7 RIGHT_NC=8 ESTOP_NC=9.
 * Driver ENABLE_N must have an external pull-up so reset/boot disables it.
 * A physical emergency-stop must also cut/disable the motor driver in hardware.
 * Arduino 5 V logic reaches the driver, NOT Raspberry Pi GPIO. Pi link is USB.
 *
 * Adjust the constants for the actual mechanism and match config.json.
 * HOMED POSITION 0 is HOME_OFFSET_MM to the right of the left switch.
 * Positive motion MUST move away from the left switch.
 */
#include <AccelStepper.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

const uint8_t STEP_PIN=2, DIR_PIN=3, ENABLE_PIN=4;
const uint8_t LEFT_PIN=7, RIGHT_PIN=8, ESTOP_PIN=9;
const bool INVERT_DIRECTION=false;
const float STEPS_PER_MM=20.0f;
const float TRAVEL_MM=540.0f;
const float MAX_SPEED_MM_S=120.0f;
const float ACCEL_MM_S2=600.0f;
const float HOME_FAST_MM_S=30.0f, HOME_SLOW_MM_S=8.0f;
const float HOME_OFFSET_MM=5.0f, HOME_CLEAR_MM=3.0f;
const uint32_t WATCHDOG_MS=250, HOME_TIMEOUT_MS=25000;
const uint32_t DEBOUNCE_MS=8, STATUS_MS=50;

// Construct without enabling outputs during global initialization.
AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN, 0, 0, false);
enum Mode { IDLE, HOME, RUN, HOLDING, FAULT };
enum HomeStage { RELEASE, SEEK, CONFIRM_FAST, BACKOFF, CLEAR, LATCH,
                 CONFIRM_SLOW, OFFSET };
Mode mode=IDLE;
HomeStage homeStage=SEEK;
bool homed=false;
const char *fault="NONE";
char session[9]="";
uint32_t lastHeartbeat=0, lastPingSeq=0, homeStarted=0;
bool havePing=false;
uint32_t switchSince=0, lastStatus=0, stateSeq=0;
long stageStart=0, clearTarget=0, seekStart=0;
char input[80];
uint8_t inputLength=0;
bool inputOverflow=false;

bool leftActive() { return digitalRead(LEFT_PIN)==HIGH; }
bool rightActive() { return digitalRead(RIGHT_PIN)==HIGH; }
bool estopActive() { return digitalRead(ESTOP_PIN)==HIGH; }
long mmSteps(float mm) { return lroundf(mm*STEPS_PER_MM); }

bool emitLine(const char *text) {
  size_t length=strlen(text);
  // Never wait for a full UART buffer: run() must remain frequently serviced.
  if (length+1 > (size_t)Serial.availableForWrite()) return false;
  Serial.write((const uint8_t*)text, length);
  Serial.write('\n');
  return true;
}

void hardStop(const char *reason) {
  motor.setCurrentPosition(motor.currentPosition()); // speed and target -> zero
  motor.disableOutputs();
  homed=false;
  fault=reason;
  mode=FAULT;
}

const char *modeName() {
  switch(mode) {
    case IDLE: return "IDLE";
    case HOME: return "HOME";
    case RUN: return "RUN";
    case HOLDING: return "HOLD";
    default: return "FAULT";
  }
}

void readyLine() {
  char line[64], travel[12], speed[12], accel[12];
  dtostrf(TRAVEL_MM,1,1,travel);
  dtostrf(MAX_SPEED_MM_S,1,1,speed);
  dtostrf(ACCEL_MM_S2,1,1,accel);
  snprintf(line,sizeof(line),"READY 1 %s %s %s %s",session,travel,speed,accel);
  emitLine(line);
}

void stateLine() {
  if (!session[0]) return;
  char line[80], x[12], v[12];
  dtostrf(motor.currentPosition()/STEPS_PER_MM,1,1,x);
  dtostrf(motor.speed()/STEPS_PER_MM,1,1,v);
  snprintf(line,sizeof(line),"STATE %s %lu %s %s %u %s %s",session,
           (unsigned long)stateSeq,x,v,homed?1:0,modeName(),fault);
  if (emitLine(line)) ++stateSeq;
}

bool validToken(const char *token) {
  if (!token || strlen(token)!=8) return false;
  for (uint8_t i=0;i<8;++i)
    if (!((token[i]>='0' && token[i]<='9') || (token[i]>='a' && token[i]<='f')))
      return false;
  return true;
}

void beginStage(HomeStage stage, float speed) {
  homeStage=stage;
  stageStart=motor.currentPosition();
  switchSince=0;
  motor.setSpeed(speed*STEPS_PER_MM);
}

void beginHome() {
  if (!havePing || (uint32_t)(millis()-lastHeartbeat)>=100) {
    hardStop("WDT"); return;
  }
  if (estopActive()) { hardStop("ESTOP"); return; }
  if (rightActive()) { hardStop("RIGHT"); return; }
  motor.setCurrentPosition(0);
  motor.setMaxSpeed(MAX_SPEED_MM_S*STEPS_PER_MM);
  motor.setAcceleration(ACCEL_MM_S2*STEPS_PER_MM);
  motor.enableOutputs();
  homed=false;
  fault="NONE";
  mode=HOME;
  homeStarted=lastHeartbeat=millis();
  seekStart=0;
  if (leftActive()) beginStage(RELEASE, HOME_SLOW_MM_S);
  else beginStage(SEEK, -HOME_FAST_MM_S);
}

void command(char *line) {
  char *save;
  char *cmd=strtok_r(line," \r\t",&save);
  if (!cmd) return;
  if (!strcmp(cmd,"STOP")) { hardStop("STOP"); return; }
  char *token=strtok_r(NULL," \r\t",&save);
  if (!strcmp(cmd,"HELLO")) {
    if (!validToken(token) || strtok_r(NULL," \r\t",&save)) return;
    if (strcmp(token,session)) {
      hardStop("SESSION");
      memcpy(session,token,9);
      // A new session is unhomed, never armed by HELLO.
      mode=IDLE; fault="NONE"; havePing=false; stateSeq=0;
    }
    readyLine();
    return;
  }
  if (!token || !session[0] || strcmp(token,session)) return;
  if (!strcmp(cmd,"PING")) {
    char *number=strtok_r(NULL," \r\t",&save), *end;
    if (!number || !*number || strtok_r(NULL," \r\t",&save)) return;
    for (char *p=number;*p;++p) if (*p<'0' || *p>'9') return;
    unsigned long seq=strtoul(number,&end,10);
    if (*end || strlen(number)>10) return;
    uint32_t delta=(uint32_t)seq-lastPingSeq;
    if (havePing && (!delta || delta>=0x80000000UL)) return;
    havePing=true; lastPingSeq=seq; lastHeartbeat=millis();
    char response[40];
    snprintf(response,sizeof(response),"PONG %s %lu",session,seq);
    emitLine(response);
    return;
  }
  if (!strcmp(cmd,"HOME")) {
    if (strtok_r(NULL," \r\t",&save)) { hardStop("PROTO"); return; }
    // Explicit HOME is the only operation allowed to clear a stopped state.
    // Do not restart an in-progress homing pass if HOME is duplicated.
    if (mode!=HOME) beginHome();
    return;
  }
  if (!strcmp(cmd,"HOLD")) {
    if (strtok_r(NULL," \r\t",&save)) { hardStop("PROTO"); return; }
    if (!homed || mode==FAULT) return;
    if (mode!=HOLDING) {
      motor.stop(); // decelerate, retaining the step-count reference
      long target=motor.targetPosition();
      target=constrain(target,0L,mmSteps(TRAVEL_MM));
      motor.moveTo(target);
      mode=HOLDING;
    }
    return;
  }
  if (!strcmp(cmd,"MOVE")) {
    char *number=strtok_r(NULL," \r\t",&save), *end;
    if (!number || strtok_r(NULL," \r\t",&save)) { hardStop("PROTO"); return; }
    float position=strtod(number,&end);
    if (end==number || *end || !isfinite(position) || position<0 || position>TRAVEL_MM) {
      hardStop("RANGE"); return;
    }
    if (!homed || (mode!=RUN && mode!=HOLDING)) return;
    motor.moveTo(mmSteps(position));
    mode=RUN;
    return;
  }
  if (homed || mode==HOME) hardStop("PROTO");
}

void readCommands() {
  // A bounded receive slice prevents command bursts from starving step pulses.
  for (uint8_t count=0;count<16 && Serial.available();++count) {
    char ch=(char)Serial.read();
    if (ch=='\n') {
      if (inputOverflow) hardStop("PROTO");
      else { input[inputLength]=0; command(input); }
      inputLength=0; inputOverflow=false;
    } else if (inputLength<sizeof(input)-1) input[inputLength++]=ch;
    else inputOverflow=true;
  }
}

bool stableState(bool condition, uint32_t now) {
  if (!condition) { switchSince=0; return false; }
  if (!switchSince) switchSince=now;
  return (uint32_t)(now-switchSince)>=DEBOUNCE_MS;
}

void homeTick(uint32_t now) {
  if ((uint32_t)(now-homeStarted)>HOME_TIMEOUT_MS) { hardStop("HTIME"); return; }
  if (labs(motor.currentPosition()-seekStart)>mmSteps(TRAVEL_MM+30)) {
    hardStop("HTRAVEL"); return;
  }
  bool active=leftActive();
  switch(homeStage) {
    case RELEASE:
      if (labs(motor.currentPosition()-stageStart)>mmSteps(20)) {
        hardStop("SWITCH"); return;
      }
      if (stableState(!active,now)) {
        seekStart=motor.currentPosition();
        beginStage(SEEK,-HOME_FAST_MM_S);
      }
      motor.runSpeed();
      break;
    case SEEK:
      if (active) {
        motor.setCurrentPosition(motor.currentPosition());
        beginStage(CONFIRM_FAST,0);
      } else motor.runSpeed();
      break;
    case CONFIRM_FAST:
      if (!active) beginStage(SEEK,-HOME_FAST_MM_S);
      else if (stableState(active,now)) beginStage(BACKOFF,HOME_SLOW_MM_S);
      break;
    case BACKOFF:
      if (labs(motor.currentPosition()-stageStart)>mmSteps(20)) {
        hardStop("SWITCH"); return;
      }
      if (stableState(!active,now)) {
        clearTarget=motor.currentPosition()+mmSteps(HOME_CLEAR_MM);
        beginStage(CLEAR,HOME_SLOW_MM_S);
      }
      motor.runSpeed();
      break;
    case CLEAR:
      if (motor.currentPosition()>=clearTarget) beginStage(LATCH,-HOME_SLOW_MM_S);
      else motor.runSpeed();
      break;
    case LATCH:
      if (active) {
        motor.setCurrentPosition(motor.currentPosition());
        beginStage(CONFIRM_SLOW,0);
      } else motor.runSpeed();
      break;
    case CONFIRM_SLOW:
      if (!active) beginStage(LATCH,-HOME_SLOW_MM_S);
      else if (stableState(active,now)) {
        motor.setCurrentPosition(0);
        motor.setMaxSpeed(HOME_SLOW_MM_S*STEPS_PER_MM);
        motor.moveTo(mmSteps(HOME_OFFSET_MM));
        homeStage=OFFSET;
      }
      break;
    case OFFSET:
      motor.run();
      if (!motor.isRunning()) {
        if (active) { hardStop("SWITCH"); return; }
        motor.setCurrentPosition(0);
        motor.setMaxSpeed(MAX_SPEED_MM_S*STEPS_PER_MM);
        homed=true; fault="NONE"; mode=RUN;
      }
      break;
  }
}

void setup() {
  pinMode(ENABLE_PIN,OUTPUT);
  digitalWrite(ENABLE_PIN,HIGH);
  pinMode(LEFT_PIN,INPUT_PULLUP);
  pinMode(RIGHT_PIN,INPUT_PULLUP);
  pinMode(ESTOP_PIN,INPUT_PULLUP);
  motor.setEnablePin(ENABLE_PIN);
  motor.setPinsInverted(INVERT_DIRECTION,false,true);
  motor.setMinPulseWidth(3);
  motor.setMaxSpeed(MAX_SPEED_MM_S*STEPS_PER_MM);
  motor.setAcceleration(ACCEL_MM_S2*STEPS_PER_MM);
  motor.disableOutputs();
  Serial.begin(115200);
}

void loop() {
  readCommands();
  uint32_t now=millis(); // commands may refresh lastHeartbeat; sample afterwards
  if (mode==HOME || homed) {
    if (estopActive()) hardStop("ESTOP");
    else if (rightActive()) hardStop("RIGHT");
    else if ((uint32_t)(now-lastHeartbeat)>WATCHDOG_MS) hardStop("WDT");
    else if (mode==HOME) homeTick(now);
    else if (leftActive()) hardStop("LEFT");
    else {
      long position=motor.currentPosition();
      if (position<0 || position>mmSteps(TRAVEL_MM) ||
          (position<=0 && motor.speed()<0) ||
          (position>=mmSteps(TRAVEL_MM) && motor.speed()>0)) hardStop("RANGE");
      else motor.run();
    }
  }
  if ((uint32_t)(now-lastStatus)>=STATUS_MS) {
    lastStatus=now;
    stateLine();
  }
}
