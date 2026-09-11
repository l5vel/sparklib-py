# SPARK Configuration Parameters

## Below is a list of all the custom enumeration types

InputMode:
 * PWM
 * CAN
 * USB

MotorType:
 * BRUSHED
 * BRUSHLESS

IdleMode:
 * COAST
 * BRAKE

Sensor:
 * NONE
 * MAIN_ENCODER
 * ANALOG
 * ALT_ENCODER
 * DUTY_CYCLE

ControlType:
 * DUTY_CYCLE
 * VELOCITY
 * VOLTAGE
 * POSITION
 * SMARTMOTION
 * SMARTVELOCITY
 * MAXMOTION_POSITION
 * MAXMOTION_VELOCITY

VoltageCompMode:
 * NO_VOLTAGE_COMP
 * CLOSED_LOOP_VOLTAGE_OUTPUT
 * NOMINAL_VOLTAGE_COMP

AccelerationStrategy:
 * TRAPEZOIDAL
 * SCURVE

AnalogMode:
 * ABSOLUTE
 * RELATIVE

CompatibilityPort:
 * DEFAULT
 * ALTERNATE_ENCODER

DutyCycleMode:
 * ABSOLUTE
 * RELATIVE
 * RELATIVE_STARTING_OFFSET

MAXMotionPositionMode:
 * TRAPEZOIDAL


## Below is a list of all the configurable parameters within the SPARK.

Parameters can be set through the CAN or USB interfaces. The parameters are saved in a different region of memory from the device firmware and persist through a firmware update.

| Name                                 | ID  | Type | Mode       | Default         | Description                                                                                                                                                                                                                                                                                                      |
|--------------------------------------|-----|------|------------|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| CAN ID | 0 | UINT32 | RW | 0 | CAN ID for this SPARK device |
| Input Mode | 1 | UINT32 | R | InputMode.PWM | Current input mode for this SPARK device |
| Motor Type | 2 | UINT32 | RW | MotorType.BRUSHLESS | The current Motor Type. Represented by the MotorType enum |
| Commutation Advance | 3 | FLOAT | RW | 0.0 | Advance Commutation by a given angle |
| Reserved | 4 | - | - | - | Reserved |
| Control Type | 5 | UINT32 | RW | ControlType.DUTY_CYCLE | The currently active Control Type. Represented by the ControlType enum |
| Idle Mode | 6 | UINT32 | RW | IdleMode.COAST | Whether to brake or coast when the setpoint is 0. Represented by the IdleMode enum |
| Input Deadband | 7 | FLOAT | RW | 0.05 | PWM input deadband |
| Reserved | 8 | - | - | - | Reserved |
| Closed Loop Control Sensor | 9 | UINT32 | RW | Sensor.NONE | Sensor for PID operation |
| Pole Pairs | 10 | UINT32 | RW | 7 | Number of pole pairs in the motor |
| Current Chop | 11 | FLOAT | RW | 115 | Start current chopping when we reach this current level |
| Current Chop Cycles | 12 | UINT32 | RW | 0 | Number of cycles before current chopping is triggered |
| P 0 | 13 | FLOAT | RW | 0.0 | Proportional gain for PID controller (index 0) |
| I 0 | 14 | FLOAT | RW | 0.0 | Integral gain for PID controller (index 0) |
| D 0 | 15 | FLOAT | RW | 0.0 | Derivative gain for PID controller (index 0) |
| F 0 | 16 | FLOAT | RW | 0.0 | Feedforward gain for PID controller (index 0) |
| IZone 0 | 17 | FLOAT | RW | 0.0 | Integral zone for PID controller (index 0) |
| D Filter 0 | 18 | FLOAT | RW | 0.0 | Derivative filter coefficient for PID controller (index 0) |
| Output Min 0 | 19 | FLOAT | RW | -1.0 | Minimum output limit for PID controller (index 0) |
| Output Max 0 | 20 | FLOAT | RW | 1.0 | Maximum output limit for PID controller (index 0) |
| P 1 | 21 | FLOAT | RW | 0.0 | Proportional gain for PID controller (index 1) |
| I 1 | 22 | FLOAT | RW | 0.0 | Integral gain for PID controller (index 1) |
| D 1 | 23 | FLOAT | RW | 0.0 | Derivative gain for PID controller (index 1) |
| F 1 | 24 | FLOAT | RW | 0.0 | Feedforward gain for PID controller (index 1) |
| IZone 1 | 25 | FLOAT | RW | 0.0 | Integral zone for PID controller (index 1) |
| D Filter 1 | 26 | FLOAT | RW | 0.0 | Derivative filter coefficient for PID controller (index 1) |
| Output Min 1 | 27 | FLOAT | RW | -1.0 | Minimum output limit for PID controller (index 1) |
| Output Max 1 | 28 | FLOAT | RW | 1.0 | Maximum output limit for PID controller (index 1) |
| P 2 | 29 | FLOAT | RW | 0.0 | Proportional gain for PID controller (index 2) |
| I 2 | 30 | FLOAT | RW | 0.0 | Integral gain for PID controller (index 2) |
| D 2 | 31 | FLOAT | RW | 0.0 | Derivative gain for PID controller (index 2) |
| F 2 | 32 | FLOAT | RW | 0.0 | Feedforward gain for PID controller (index 2) |
| IZone 2 | 33 | FLOAT | RW | 0.0 | Integral zone for PID controller (index 2) |
| D Filter 2 | 34 | FLOAT | RW | 0.0 | Derivative filter coefficient for PID controller (index 2) |
| Output Min 2 | 35 | FLOAT | RW | -1.0 | Minimum output limit for PID controller (index 2) |
| Output Max 2 | 36 | FLOAT | RW | 1.0 | Maximum output limit for PID controller (index 2) |
| P 3 | 37 | FLOAT | RW | 0.0 | Proportional gain for PID controller (index 3) |
| I 3 | 38 | FLOAT | RW | 0.0 | Integral gain for PID controller (index 3) |
| D 3 | 39 | FLOAT | RW | 0.0 | Derivative gain for PID controller (index 3) |
| F 3 | 40 | FLOAT | RW | 0.0 | Feedforward gain for PID controller (index 3) |
| IZone 3 | 41 | FLOAT | RW | 0.0 | Integral zone for PID controller (index 3) |
| D Filter 3 | 42 | FLOAT | RW | 0.0 | Derivative filter coefficient for PID controller (index 3) |
| Output Min 3 | 43 | FLOAT | RW | -1.0 | Minimum output limit for PID controller (index 3) |
| Output Max 3 | 44 | FLOAT | RW | 1.0 | Maximum output limit for PID controller (index 3) |
| Inverted | 45 | BOOL | RW | false | Whether to invert the motor |
| Reserved | 46 | - | - | - | Reserved |
| Reserved | 47 | - | - | - | Reserved |
| Reserved | 48 | - | - | - | Reserved |
| Reserved | 49 | - | - | - | Reserved |
| Limit Switch Fwd Polarity | 50 | BOOL | RW | false | Forward limit switch polarity setting |
| Limit Switch Rev Polarity | 51 | BOOL | RW | false | Reverse limit switch polarity setting |
| Hard Limit Fwd En | 52 | BOOL | RW | true | Enable forward hard limit switch |
| Hard Limit Rev En | 53 | BOOL | RW | true | Enable reverse hard limit switch |
| Soft Limit Fwd En | 54 | BOOL | RW | false | Enable forward soft limit |
| Soft Limit Rev En | 55 | BOOL | RW | false | Enable reverse soft limit |
| Open Loop Ramp Rate | 56 | FLOAT | RW | 0.0 | Ramp rate for open-loop control |
| Follower ID | 57 | UINT32 | RW | 0 | Legacy follower arbitration ID |
| Follower Config | 58 | UINT32 | RW | 0 | Legacy follower configuration |
| Smart Current Stall Limit | 59 | UINT32 | RW | 80 | Current limit for stall conditions in smart current mode |
| Smart Current Free Limit | 60 | UINT32 | RW | 20 | Current limit for free running in smart current mode |
| Smart Current Config | 61 | UINT32 | RW | 10000 | Configuration parameter for smart current mode |
| Smart Current Reserved | 62 | UINT32 | RW | 0 | Reserved parameter for smart current mode |
| Motor Kv | 63 | UINT32 | RW | 480 | Motor velocity constant (Kv) |
| Reserved | 64 | - | - | - | Reserved |
| Reserved | 65 | - | - | - | Reserved |
| Reserved | 66 | - | - | - | Reserved |
| Reserved | 67 | - | - | - | Reserved |
| Reserved | 68 | - | - | - | Reserved |
| Encoder Counts Per Rev | 69 | UINT32 | RW | 4096 | Number of encoder counts per revolution |
| Encoder Average Depth | 70 | UINT32 | RW | 64 | Depth of encoder averaging filter |
| Encoder Sample Delta | 71 | UINT32 | RW | 200 | Time delta between encoder samples |
| Encoder Inverted | 72 | BOOL | RW | false | Invert encoder direction |
| Reserved | 73 | - | - | - | Reserved |
| Voltage Compensation Mode | 74 | UINT32 | RW | VoltageCompMode.NO_VOLTAGE_COMP | How voltage compensation should be handled. Represented by the VoltageCompMode enum |
| Compensated Nominal Voltage | 75 | FLOAT | RW | 0 | Input Voltage to use for voltage compensation |
| SmartMotion Max Velocity 0 | 76 | FLOAT | RW | 0 | Max Velocity for Smart Motion slot 0 |
| SmartMotion Max Accel 0 | 77 | FLOAT | RW | 0 | Max Acceleration for Smart Motion slot 0 |
| SmartMotion Min Vel Output 0 | 78 | FLOAT | RW | 0 | Min Velocity for Smart Motion slot 0 |
| SmartMotion Allowed Closed Loop Error 0 | 79 | FLOAT | RW | 0 | Allowed error in native units for Smart Motion slot 0 |
| SmartMotion Accel Strategy 0 | 80 | UINT32 | RW | AccelerationStrategy.TRAPEZOIDAL | Acceleration Strategy for Smart Motion slot 0 |
| SmartMotion Max Velocity 1 | 81 | FLOAT | RW | 0 | Max Velocity for Smart Motion slot 1 |
| SmartMotion Max Accel 1 | 82 | FLOAT | RW | 0 | Max Acceleration for Smart Motion slot 1 |
| SmartMotion Min Vel Output 1 | 83 | FLOAT | RW | 0 | Min Velocity for Smart Motion slot 1 |
| SmartMotion Allowed Closed Loop Error 1 | 84 | FLOAT | RW | 0 | Allowed error in native units for Smart Motion slot 1 |
| SmartMotion Accel Strategy 1 | 85 | UINT32 | RW | AccelerationStrategy.TRAPEZOIDAL | Acceleration Strategy for Smart Motion slot 1 |
| SmartMotion Max Velocity 2 | 86 | FLOAT | RW | 0 | Max Velocity for Smart Motion slot 2 |
| SmartMotion Max Accel 2 | 87 | FLOAT | RW | 0 | Max Acceleration for Smart Motion slot 2 |
| SmartMotion Min Vel Output 2 | 88 | FLOAT | RW | 0 | Minimum velocity output for smart motion slot 2 |
| SmartMotion Allowed Closed Loop Error 2 | 89 | FLOAT | RW | 0 | Allowed closed-loop error for smart motion slot 2 |
| SmartMotion Accel Strategy 2 | 90 | UINT32 | RW | AccelerationStrategy.TRAPEZOIDAL | Acceleration strategy for smart motion slot 2 |
| SmartMotion Max Velocity 3 | 91 | FLOAT | RW | 0 | Maximum velocity for smart motion slot 3 |
| SmartMotion Max Accel 3 | 92 | FLOAT | RW | 0 | Maximum acceleration for smart motion slot 3 |
| SmartMotion Min Vel Output 3 | 93 | FLOAT | RW | 0 | Minimum velocity output for smart motion slot 3 |
| SmartMotion Allowed Closed Loop Error 3 | 94 | FLOAT | RW | 0 | Allowed closed-loop error for smart motion slot 3 |
| SmartMotion Accel Strategy 3 | 95 | UINT32 | RW | AccelerationStrategy.TRAPEZOIDAL | Acceleration strategy for smart motion slot 3 |
| I Max Accum 0 | 96 | FLOAT | RW | 0 | Maximum accumulator value for I control (slot 0) |
| Reserved | 97 | - | - | - | Reserved |
| Reserved | 98 | - | - | - | Reserved |
| Reserved | 99 | - | - | - | Reserved |
| I Max Accum 1 | 100 | FLOAT | RW | 0 | Maximum accumulator value for I control (slot 1) |
| Reserved | 101 | - | - | - | Reserved |
| Reserved | 102 | - | - | - | Reserved |
| Reserved | 103 | - | - | - | Reserved |
| I Max Accum 2 | 104 | FLOAT | RW | 0 | Maximum accumulator value for I control (slot 2) |
| Reserved | 105 | - | - | - | Reserved |
| Reserved | 106 | - | - | - | Reserved |
| Reserved | 107 | - | - | - | Reserved |
| I Max Accum 3 | 108 | FLOAT | RW | 0 | Maximum accumulator value for I control (slot 3) |
| Reserved | 109 | - | - | - | Reserved |
| Reserved | 110 | - | - | - | Reserved |
| Reserved | 111 | - | - | - | Reserved |
| Position Conversion Factor | 112 | FLOAT | RW | 1.0 | Factor to convert encoder counts or other position units to the desired position units. |
| Velocity Conversion Factor | 113 | FLOAT | RW | 1.0 | Factor to convert encoder counts or other velocity units to the desired velocity units. |
| Closed Loop Ramp Rate | 114 | FLOAT | RW | 0 | Ramp rate for closed-loop control, in DC per second |
| Soft Limit Forward | 115 | FLOAT | RW | 0 | Forward soft limit value |
| Soft Limit Reverse | 116 | FLOAT | RW | 0 | Reverse soft limit value |
| Reserved | 117 | - | - | - | Reserved |
| Reserved | 118 | - | - | - | Reserved |
| Analog Position Conversion | 119 | FLOAT | RW | 1.0 | Conversion factor for position from analog sensor. This value is multiplied by the voltage to give an output value |
| Analog Velocity Conversion | 120 | FLOAT | RW | 1.0 | Conversion factor for velocity from analog sensor. This value is multiplied by the voltage to give an output value |
| Analog Average Depth | 121 | UINT32 | RW | 64 | Number of samples to average for velocity data based on analog encoder input. This value can be between 1 and 64 |
| Analog Sensor Mode | 122 | UINT32 | RW | AnalogMode.ABSOLUTE | Absolute: In this mode the sensor position is always read as voltage * conversion factor and reads the absolute position of the sensor. In this mode setPosition() does not have an effect.\n 1 Relative: In this mode the voltage difference is summed to calculate a relative position. |
| Analog Inverted | 123 | BOOL | RW | 0 | When inverted, the voltage is calculated as (ADC Full Scale - ADC Reading). This means that for absolute mode, the sensor value is 3.3V - voltage. In relative mode the direction is reversed. |
| Analog Sample Delta | 124 | UINT32 | RW | 200 | Delta time between samples for velocity measurement. |
| Reserved | 125 | - | - | - | Reserved |
| Reserved | 126 | - | - | - | Reserved |
| Compatibility Port Config | 127 | UINT32 | RW | CompatibilityPort.DEFAULT | DEFAULT: Default configuration using limit switches.\n ALTERNATE_ENCODER: Alternate Encoder Mode - limit switches are disabled and alternate encoder is enabled. Represented by the CompatibilityPort enum |
| Alt Encoder Counts Per Rev | 128 | UINT32 | RW | 4096 | Number of encoder counts in a single revolution, counting every edge on the A and B lines of a quadrature encoder. (Note: This is different than the CPR spec of the encoder which is 'Cycles per revolution'. This value is 4 * CPR.) |
| Alt Encoder Average Depth | 129 | UINT32 | RW | 64 | Number of samples to average for velocity data based on quadrature encoder input. This value can be between 1 and 64. |
| Alt Encoder Sample Delta | 130 | UINT32 | RW | 200 | Delta time value for encoder velocity measurement in 500us increments. The velocity calculation will take delta the current sample, and the sample x * 500us behind, and divide by this the sample delta time. |
| Alt Encoder Inverted | 131 | BOOL | RW | false | Invert the phase of the encoder sensor. This is useful when the motor direction is opposite of the motor direction. |
| Alt Encoder Position Conversion | 132 | FLOAT | RW | 1.0 | Value multiplied by the native units (rotations) of the encoder for position. |
| Alt Encoder Velocity Conversion | 133 | FLOAT | RW | 1.0 | Value multiplied by the native units (rotations) of the encoder for velocity. |
| Reserved | 134 | - | - | - | Reserved |
| Reserved | 135 | - | - | - | Reserved |
| Uvw Sensor Sample Rate | 136 | FLOAT | RW | 0.03125 | Sample period for UVW sensor |
| Uvw Sensor Average Depth | 137 | UINT32 | RW | 3 | Number of samples to average for velocity data based on UVW encoder input. |
| Num Parameters | 138 | UINT32 | R | 137 | Number of parameters in the table, including unused values in the middle of the table. |
| Duty Cycle Position Factor | 139 | FLOAT | RW | 1.0 | Value multiplied by the native units (rotations) of the encoder for position. |
| Duty Cycle Velocity Factor | 140 | FLOAT | RW | 1.0 | Value multiplied by the native units (rotations) of the encoder for velocity. |
| Duty Cycle Inverted | 141 | BOOL | RW | false | Invert the phase of the duty cycle sensor. This is useful when the motor direction is opposite of the encoder direction |
| Duty Cycle Sensor Mode | 142 | UINT32 | RW | DutyCycleMode.ABSOLUTE | Mode for the duty cycle sensor. Represented by the DutyCycleMode enum |
| Duty Cycle Average Depth | 143 | UINT32 | RW | 7 | Log base 2 of the number of samples to average for velocity data based on duty cycle encoder input |
| Reserved | 144 | - | - | - | Reserved |
| Duty Cycle Offset Legacy | 145 | FLOAT | RW | 0 | Legacy method of setting duty cycle offset. This parameter sets the offset after inversion is accounted for. |
| Reserved | 146 | - | - | - | Reserved |
| Reserved | 147 | - | - | - | Reserved |
| Reserved | 148 | - | - | - | Reserved |
| Position PID Wrap Enable | 149 | BOOL | RW | false | Allow PID controller to wrap around |
| Position PID Min Input | 150 | FLOAT | RW | 0 | Lower value for PID wrapping |
| Position PID Max Input | 151 | FLOAT | RW | 0 | Upper value for PID wrapping |
| Duty Cycle Zero Centered | 152 | BOOL | RW | false | Center Duty Cycle readings at 0 |
| Duty Cycle Sensor Prescaler | 153 | UINT32 | RW | 17 | The Duty Cycle Sensor will set its time base to the device clock divided by (value + 1). For Flex, the device clock is 170MHz, and for MAX, the device clock is 72MHz |
| Duty Cycle Offset | 154 | FLOAT | RW | 0 | Offset Duty Cycle encoder readings by a fixed value (0 to 1) |
| Product Id | 155 | UINT32 | R | 0 | Read-only view of the REV Product Number of the device. |
| Device Major Version | 156 | UINT32 | R | 0 | Major revision of the device |
| Device Minor Version | 157 | UINT32 | R | 4294967295 | Minor revision of the device |
| Status 0 Period | 158 | UINT32 | RW | 10 | Status frame 0 period, in us |
| Status 1 Period | 159 | UINT32 | RW | 250 | Status frame 1 period, in us |
| Status 2 Period | 160 | UINT32 | RW | 20 | Status frame 2 period, in us |
| Status 3 Period | 161 | UINT32 | RW | 20 | Status frame 3 period, in us |
| Status 4 Period | 162 | UINT32 | RW | 20 | Status frame 4 period, in us |
| Status 5 Period | 163 | UINT32 | RW | 20 | Status frame 5 period, in us |
| Status 6 Period | 164 | UINT32 | RW | 20 | Status frame 6 period, in us |
| Status 7 Period | 165 | UINT32 | RW | 20 | Status frame 7 period, in us |
| MAXMotion Max Velocity 0 | 166 | FLOAT | RW | 0 | Maximum Velocity MAXMotion will reach in position mode for slot 0 |
| MAXMotion Max Accel 0 | 167 | FLOAT | RW | 0 | Maximum Acceleration MAXMotion will reach in position mode or velocity mode for slot 0 |
| MAXMotion Max Jerk 0 | 168 | FLOAT | RW | 0 | Not currently implemented |
| MAXMotion Allowed Closed Loop Error 0 | 169 | FLOAT | RW | 0 | Maximum allowed closed loop error for slot 0 |
| MAXMotion Position Mode 0 | 170 | UINT32 | RW | MAXMotionPositionMode.TRAPEZOIDAL | Position mode for slot 0. Represented by the MAXMotionPositionMode enum |
| MAXMotion Max Velocity 1 | 171 | FLOAT | RW | 0 | Maximum Velocity MAXMotion will reach in position mode for slot 1 |
| MAXMotion Max Accel 1 | 172 | FLOAT | RW | 0 | Maximum Acceleration MAXMotion will reach in position mode or velocity mode for slot 1 |
| MAXMotion Max Jerk 1 | 173 | FLOAT | RW | 0 | Not currently implemented |
| MAXMotion Allowed Closed Loop Error 1 | 174 | FLOAT | RW | 0 | Maximum allowed closed loop error for slot 1 |
| MAXMotion Position Mode 1 | 175 | UINT32 | RW | MAXMotionPositionMode.TRAPEZOIDAL | Position mode for slot 1. Represented by the MAXMotionPositionMode enum |
| MAXMotion Max Velocity 2 | 176 | FLOAT | RW | 0 | Maximum Velocity MAXMotion will reach in position mode for slot 2 |
| MAXMotion Max Accel 2 | 177 | FLOAT | RW | 0 | Maximum Acceleration MAXMotion will reach in position mode or velocity mode for slot 2 |
| MAXMotion Max Jerk 2 | 178 | FLOAT | RW | 0 | Not currently implemented |
| MAXMotion Allowed Closed Loop Error 2 | 179 | FLOAT | RW | 0 | Maximum allowed closed loop error for slot 2 |
| MAXMotion Position Mode 2 | 180 | UINT32 | RW | MAXMotionPositionMode.TRAPEZOIDAL | Position mode for slot 1. Represented by the MAXMotionPositionMode enum |
| MAXMotion Max Velocity 3 | 181 | FLOAT | RW | 0 | Maximum Velocity MAXMotion will reach in position mode for slot 3 |
| MAXMotion Max Accel 3 | 182 | FLOAT | RW | 0 | Maximum Acceleration MAXMotion will reach in position mode or velocity mode for slot 3 |
| MAXMotion Max Jerk 3 | 183 | FLOAT | RW | 0 | Not currently implemented |
| MAXMotion Allowed Closed Loop Error 3 | 184 | FLOAT | RW | 0 | Maximum allowed closed loop error for slot 3 |
| MAXMotion Position Mode 3 | 185 | UINT32 | RW | MAXMotionPositionMode.TRAPEZOIDAL | Position mode for slot 3. Represented by the MAXMotionPositionMode enum |
| Force Enable Status 0 | 186 | BOOL | RW | false | Force Status Frame 0 to be enabled |
| Force Enable Status 1 | 187 | BOOL | RW | false | Force Status Frame 1 to be enabled |
| Force Enable Status 2 | 188 | BOOL | RW | false | Force Status Frame 2 to be enabled |
| Force Enable Status 3 | 189 | BOOL | RW | false | Force Status Frame 3 to be enabled |
| Force Enable Status 4 | 190 | BOOL | RW | false | Force Status Frame 4 to be enabled |
| Force Enable Status 5 | 191 | BOOL | RW | false | Force Status Frame 5 to be enabled |
| Force Enable Status 6 | 192 | BOOL | RW | false | Force Status Frame 6 to be enabled |
| Force Enable Status 7 | 193 | BOOL | RW | false | Force Status Frame 7 to be enabled |
| Follower Mode Leader Id | 194 | UINT32 | RW | false | CAN ID of a SPARK device to follow |
| Follower Mode Is Inverted | 195 | BOOL | RW | false | Invert the setpoint when following. This is useful if this motor must spin opposite the leader |
| Duty Cycle Encoder Start Pulse Us | 196 | FLOAT | RW | 0 | Tells this SPARK that the duty cycle sensor will always output a start (high) pulse of the given microseconds |
| Duty Cycle Encoder End Pulse Us | 197 | FLOAT | RW | 0 | Tells this SPARK that the duty cycle sensor will always output an end (low) pulse of the given microseconds |
| Param Table Version | 198 | UINT32 | R | 0 | Hardware Client Parameter Table Version |
