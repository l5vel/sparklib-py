/*
 * Copyright (c) 2024-2025 REV Robotics
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of REV Robotics nor the names of its
 *    contributors may be used to endorse or promote products derived from
 *    this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

#pragma once

namespace rev::spark {

enum SparkParameter : uint8_t {
    kCanID = 0,                                 // uint32_t
    kInputMode = 1,                             // uint32_t
    kMotorType = 2,                             // uint32_t
    kCommAdvance = 3,                           // float
    kSensorType = 4,                            // uint32_t
    kCtrlType = 5,                              // uint32_t
    kIdleMode = 6,                              // uint32_t
    kInputDeadband = 7,                         // float
    kLegacyFeedbackSensorPID0 = 8,              // uint32_t
    kClosedLoopControlSensor = 9,               // uint32_t
    kPolePairs = 10,                            // uint32_t
    kCurrentChop = 11,                          // float
    kCurrentChopCycles = 12,                    // uint32_t
    kP_0 = 13,                                  // float
    kI_0 = 14,                                  // float
    kD_0 = 15,                                  // float
    kF_0 = 16,                                  // float
    kIZone_0 = 17,                              // float
    kDFilter_0 = 18,                            // float
    kOutputMin_0 = 19,                          // float
    kOutputMax_0 = 20,                          // float
    kP_1 = 21,                                  // float
    kI_1 = 22,                                  // float
    kD_1 = 23,                                  // float
    kF_1 = 24,                                  // float
    kIZone_1 = 25,                              // float
    kDFilter_1 = 26,                            // float
    kOutputMin_1 = 27,                          // float
    kOutputMax_1 = 28,                          // float
    kP_2 = 29,                                  // float
    kI_2 = 30,                                  // float
    kD_2 = 31,                                  // float
    kF_2 = 32,                                  // float
    kIZone_2 = 33,                              // float
    kDFilter_2 = 34,                            // float
    kOutputMin_2 = 35,                          // float
    kOutputMax_2 = 36,                          // float
    kP_3 = 37,                                  // float
    kI_3 = 38,                                  // float
    kD_3 = 39,                                  // float
    kF_3 = 40,                                  // float
    kIZone_3 = 41,                              // float
    kDFilter_3 = 42,                            // float
    kOutputMin_3 = 43,                          // float
    kOutputMax_3 = 44,                          // float
    kInverted = 45,                             // bool
    kOutputRatio = 46,                          // float
    kSerialNumberLow = 47,                      // uint32_t
    kSerialNumberMid = 48,                      // uint32_t
    kSerialNumberHigh = 49,                     // uint32_t
    kLimitSwitchFwdPolarity = 50,               // bool
    kLimitSwitchRevPolarity = 51,               // bool
    kHardLimitFwdEn = 52,                       // bool
    kHardLimitRevEn = 53,                       // bool
    kSoftLimitFwdEn = 54,                       // bool
    kSoftLimitRevEn = 55,                       // bool
    kRampRate = 56,                             // float
    kFollowerID = 57,                           // uint32_t
    kFollowerConfig = 58,                       // uint32_t
    kSmartCurrentStallLimit = 59,               // uint32_t
    kSmartCurrentFreeLimit = 60,                // uint32_t
    kSmartCurrentConfig = 61,                   // uint32_t
    kSmartCurrentReserved = 62,                 // uint32_t
    kMotorKv = 63,                              // uint32_t
    kMotorR = 64,                               // uint32_t
    kMotorL = 65,                               // uint32_t
    kMotorRsvd1 = 66,                           // uint32_t
    kMotorRsvd2 = 67,                           // uint32_t
    kMotorRsvd3 = 68,                           // uint32_t
    kEncoderCountsPerRev = 69,                  // uint32_t
    kEncoderAverageDepth = 70,                  // uint32_t
    kEncoderSampleDelta = 71,                   // uint32_t
    kEncoderInverted = 72,                      // bool
    kEncoderRsvd1 = 73,                         // uint32_t
    kVoltageCompMode = 74,                      // uint32_t
    kCompensatedNominalVoltage = 75,            // float
    kSmartMotionMaxVelocity_0 = 76,             // float
    kSmartMotionMaxAccel_0 = 77,                // float
    kSmartMotionMinVelOutput_0 = 78,            // float
    kSmartMotionAllowedClosedLoopError_0 = 79,  // float
    kSmartMotionAccelStrategy_0 = 80,           // float
    kSmartMotionMaxVelocity_1 = 81,             // float
    kSmartMotionMaxAccel_1 = 82,                // float
    kSmartMotionMinVelOutput_1 = 83,            // float
    kSmartMotionAllowedClosedLoopError_1 = 84,  // float
    kSmartMotionAccelStrategy_1 = 85,           // float
    kSmartMotionMaxVelocity_2 = 86,             // float
    kSmartMotionMaxAccel_2 = 87,                // float
    kSmartMotionMinVelOutput_2 = 88,            // float
    kSmartMotionAllowedClosedLoopError_2 = 89,  // float
    kSmartMotionAccelStrategy_2 = 90,           // float
    kSmartMotionMaxVelocity_3 = 91,             // float
    kSmartMotionMaxAccel_3 = 92,                // float
    kSmartMotionMinVelOutput_3 = 93,            // float
    kSmartMotionAllowedClosedLoopError_3 = 94,  // float
    kSmartMotionAccelStrategy_3 = 95,           // float
    kIMaxAccum_0 = 96,                          // float
    kSlot3Placeholder1_0 = 97,                  // float
    kSlot3Placeholder2_0 = 98,                  // float
    kSlot3Placeholder3_0 = 99,                  // float
    kIMaxAccum_1 = 100,                         // float
    kSlot3Placeholder1_1 = 101,                 // float
    kSlot3Placeholder2_1 = 102,                 // float
    kSlot3Placeholder3_1 = 103,                 // float
    kIMaxAccum_2 = 104,                         // float
    kSlot3Placeholder1_2 = 105,                 // float
    kSlot3Placeholder2_2 = 106,                 // float
    kSlot3Placeholder3_2 = 107,                 // float
    kIMaxAccum_3 = 108,                         // float
    kSlot3Placeholder1_3 = 109,                 // float
    kSlot3Placeholder2_3 = 110,                 // float
    kSlot3Placeholder3_3 = 111,                 // float
    kPositionConversionFactor = 112,            // float
    kVelocityConversionFactor = 113,            // float
    kClosedLoopRampRate = 114,                  // float
    kSoftLimitFwd = 115,                        // float
    kSoftLimitRev = 116,                        // float
    kSoftLimitRsvd0 = 117,                      // uint32_t
    kSoftLimitRsvd1 = 118,                      // uint32_t
    kAnalogRevPerVolt = 119,                    // float
    kAnalogRotationsPerVoltSec = 120,           // float
    kAnalogAverageDepth = 121,                  // uint32_t
    kAnalogSensorMode = 122,                    // uint32_t
    kAnalogInverted = 123,                      // bool
    kAnalogSampleDelta = 124,                   // uint32_t
    kAnalogRsvd0 = 125,                         // uint32_t
    kAnalogRsvd1 = 126,                         // uint32_t
    kDataPortConfig = 127,                      // uint32_t
    kAltEncoderCountsPerRev = 128,              // uint32_t
    kAltEncoderAverageDepth = 129,              // uint32_t
    kAltEncoderSampleDelta = 130,               // uint32_t
    kAltEncoderInverted = 131,                  // bool
    kAltEncodePositionFactor = 132,             // float
    kAltEncoderVelocityFactor = 133,            // float
    kAltEncoderRsvd0 = 134,                     // uint32_t
    kAltEncoderRsvd1 = 135,                     // uint32_t
    kHallSensorSampleRate = 136,                // float
    kHallSensorAverageDepth = 137,              // uint32_t
    kNumParameters = 138,                       // uint32_t
    kDutyCyclePositionFactor = 139,             // float
    kDutyCycleVelocityFactor = 140,             // float
    kDutyCycleInverted = 141,                   // bool
    kDutyCycleSensorMode = 142,                 // uint32_t
    kDutyCycleAverageDepth = 143,               // uint32_t
    kDutyCycleSampleDelta = 144,                // uint32_t
    kDutyCycleOffsetv1p6p2 = 145,               // float
    kDutyCycleRsvd0 = 146,                      // uint32_t
    kDutyCycleRsvd1 = 147,                      // uint32_t
    kDutyCycleRsvd2 = 148,                      // uint32_t
    kPositionPIDWrapEnable = 149,               // bool
    kPositionPIDMinInput = 150,                 // float
    kPositionPIDMaxInput = 151,                 // float
    kDutyCycleZeroCentered = 152,               // bool
    kDutyCyclePrescaler = 153,                  // uint32_t
    kDutyCycleOffset = 154,                     // float
    kProductId = 155,                           // uint32_t
    kDeviceMajorVersion = 156,                  // uint32_t
    kDeviceMinorVersion = 157,                  // uint32_t
    kStatus0Period = 158,                       // uint32_t
    kStatus1Period = 159,                       // uint32_t
    kStatus2Period = 160,                       // uint32_t
    kStatus3Period = 161,                       // uint32_t
    kStatus4Period = 162,                       // uint32_t
    kStatus5Period = 163,                       // uint32_t
    kStatus6Period = 164,                       // uint32_t
    kStatus7Period = 165,                       // uint32_t
    kMAXMotionMaxVelocity_0 = 166,              // float
    kMAXMotionMaxAccel_0 = 167,                 // float
    kMAXMotionMaxJerk_0 = 168,                  // float
    kMAXMotionAllowedClosedLoopError_0 = 169,   // float
    kMAXMotionPositionMode_0 = 170,             // uint32_t
    kMAXMotionMaxVelocity_1 = 171,              // float
    kMAXMotionMaxAccel_1 = 172,                 // float
    kMAXMotionMaxJerk_1 = 173,                  // float
    kMAXMotionAllowedClosedLoopError_1 = 174,   // float
    kMAXMotionPositionMode_1 = 175,             // uint32_t
    kMAXMotionMaxVelocity_2 = 176,              // float
    kMAXMotionMaxAccel_2 = 177,                 // float
    kMAXMotionMaxJerk_2 = 178,                  // float
    kMAXMotionAllowedClosedLoopError_2 = 179,   // float
    kMAXMotionPositionMode_2 = 180,             // uint32_t
    kMAXMotionMaxVelocity_3 = 181,              // float
    kMAXMotionMaxAccel_3 = 182,                 // float
    kMAXMotionMaxJerk_3 = 183,                  // float
    kMAXMotionAllowedClosedLoopError_3 = 184,   // float
    kMAXMotionPositionMode_3 = 185,             // uint32_t
    kForceEnableStatus0 = 186,                  // uint32_t
    kForceEnableStatus1 = 187,                  // uint32_t
    kForceEnableStatus2 = 188,                  // uint32_t
    kForceEnableStatus3 = 189,                  // uint32_t
    kForceEnableStatus4 = 190,                  // uint32_t
    kForceEnableStatus5 = 191,                  // uint32_t
    kForceEnableStatus6 = 192,                  // uint32_t
    kForceEnableStatus7 = 193,                  // uint32_t
    kFollowerModeLeaderId = 194,                // uint32_t
    kFollowerModeIsInverted = 195,              // bool
    kDutyCycleEncoderStartPulseUs = 196,        // float
    kDutyCycleEncoderEndPulseUs = 197,          // float
    kParamTableVersion = 198                    // uint32_t
};

}  // namespace rev::spark
