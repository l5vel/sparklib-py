/*
 * Copyright (c) 2024-2026 REV Robotics
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

/*
 * This file is auto-generated. Do NOT modify it directly.
 * See https://github.com/REVrobotics/SparkParameters
 */

#pragma once

#include <stdint.h>

namespace rev::spark {

enum SparkParameter : uint8_t {
    kCANID = 0,  // uint32_t
    kInputMode = 1,  // uint32_t
    kMotorType = 2,  // uint32_t
    kCommutationAdvance = 3,  // float
    kControlType = 5,  // uint32_t
    kIdleMode = 6,  // uint32_t
    kInputDeadband = 7,  // float
    kClosedLoopControlSensor = 9,  // uint32_t
    kPolePairs = 10,  // uint32_t
    kCurrentChop = 11,  // float
    kCurrentChopCycles = 12,  // uint32_t
    kP_0 = 13,  // float
    kI_0 = 14,  // float
    kD_0 = 15,  // float
    kV_0 = 16,  // float
    kIZone_0 = 17,  // float
    kDFilter_0 = 18,  // float
    kOutputMin_0 = 19,  // float
    kOutputMax_0 = 20,  // float
    kP_1 = 21,  // float
    kI_1 = 22,  // float
    kD_1 = 23,  // float
    kV_1 = 24,  // float
    kIZone_1 = 25,  // float
    kDFilter_1 = 26,  // float
    kOutputMin_1 = 27,  // float
    kOutputMax_1 = 28,  // float
    kP_2 = 29,  // float
    kI_2 = 30,  // float
    kD_2 = 31,  // float
    kV_2 = 32,  // float
    kIZone_2 = 33,  // float
    kDFilter_2 = 34,  // float
    kOutputMin_2 = 35,  // float
    kOutputMax_2 = 36,  // float
    kP_3 = 37,  // float
    kI_3 = 38,  // float
    kD_3 = 39,  // float
    kV_3 = 40,  // float
    kIZone_3 = 41,  // float
    kDFilter_3 = 42,  // float
    kOutputMin_3 = 43,  // float
    kOutputMax_3 = 44,  // float
    kInverted = 45,  // bool
    kLimitSwitchFwdPolarity = 50,  // bool
    kLimitSwitchRevPolarity = 51,  // bool
    kHardLimitFwdEn = 52,  // uint32_t
    kHardLimitRevEn = 53,  // uint32_t
    kSoftLimitFwdEn = 54,  // bool
    kSoftLimitRevEn = 55,  // bool
    kOpenLoopRampRate = 56,  // float
    kLegacyFollowerID = 57,  // uint32_t
    kLegacyFollowerConfig = 58,  // uint32_t
    kSmartCurrentStallLimit = 59,  // uint32_t
    kSmartCurrentFreeLimit = 60,  // uint32_t
    kSmartCurrentConfig = 61,  // uint32_t
    kSmartCurrentReserved = 62,  // uint32_t
    kMotorKv = 63,  // uint32_t
    kEncoderCountsPerRev = 69,  // uint32_t
    kEncoderAverageDepth = 70,  // uint32_t
    kEncoderSampleDelta = 71,  // uint32_t
    kEncoderInverted = 72,  // bool
    kVoltageCompensationMode = 74,  // uint32_t
    kCompensatedNominalVoltage = 75,  // float
    kIMaxAccum_0 = 96,  // float
    kAllowedClosedLoopError_0 = 97,  // float
    kIMaxAccum_1 = 100,  // float
    kAllowedClosedLoopError_1 = 101,  // float
    kIMaxAccum_2 = 104,  // float
    kAllowedClosedLoopError_2 = 105,  // float
    kIMaxAccum_3 = 108,  // float
    kAllowedClosedLoopError_3 = 109,  // float
    kPositionConversionFactor = 112,  // float
    kVelocityConversionFactor = 113,  // float
    kClosedLoopRampRate = 114,  // float
    kSoftLimitForward = 115,  // float
    kSoftLimitReverse = 116,  // float
    kAnalogPositionConversion = 119,  // float
    kAnalogVelocityConversion = 120,  // float
    kAnalogAverageDepth = 121,  // uint32_t
    kAnalogSensorMode = 122,  // uint32_t
    kAnalogInverted = 123,  // bool
    kAnalogSampleDelta = 124,  // uint32_t
    kCompatibilityPortConfig = 127,  // uint32_t
    kAltEncoderCountsPerRev = 128,  // uint32_t
    kAltEncoderAverageDepth = 129,  // uint32_t
    kAltEncoderSampleDelta = 130,  // uint32_t
    kAltEncoderInverted = 131,  // bool
    kAltEncoderPositionConversion = 132,  // float
    kAltEncoderVelocityConversion = 133,  // float
    kUvwSensorSampleRate = 136,  // float
    kUvwSensorAverageDepth = 137,  // uint32_t
    kNumParameters = 138,  // uint32_t
    kDutyCyclePositionFactor = 139,  // float
    kDutyCycleVelocityFactor = 140,  // float
    kDutyCycleInverted = 141,  // bool
    kDutyCycleSensorMode = 142,  // uint32_t
    kDutyCycleAverageDepth = 143,  // uint32_t
    kDutyCycleOffsetLegacy = 145,  // float
    kPositionPIDWrapEnable = 149,  // bool
    kPositionPIDMinInput = 150,  // float
    kPositionPIDMaxInput = 151,  // float
    kDutyCycleZeroCentered = 152,  // bool
    kDutyCycleSensorPrescaler = 153,  // uint32_t
    kDutyCycleOffset = 154,  // float
    kProductId = 155,  // uint32_t
    kDeviceMajorVersion = 156,  // uint32_t
    kDeviceMinorVersion = 157,  // uint32_t
    kStatus0Period = 158,  // uint32_t
    kStatus1Period = 159,  // uint32_t
    kStatus2Period = 160,  // uint32_t
    kStatus3Period = 161,  // uint32_t
    kStatus4Period = 162,  // uint32_t
    kStatus5Period = 163,  // uint32_t
    kStatus6Period = 164,  // uint32_t
    kStatus7Period = 165,  // uint32_t
    kMAXMotionCruiseVelocity_0 = 166,  // float
    kMAXMotionMaxAccel_0 = 167,  // float
    kMAXMotionMaxJerk_0 = 168,  // float
    kMAXMotionAllowedProfileError_0 = 169,  // float
    kMAXMotionPositionMode_0 = 170,  // uint32_t
    kMAXMotionCruiseVelocity_1 = 171,  // float
    kMAXMotionMaxAccel_1 = 172,  // float
    kMAXMotionMaxJerk_1 = 173,  // float
    kMAXMotionAllowedProfileError_1 = 174,  // float
    kMAXMotionPositionMode_1 = 175,  // uint32_t
    kMAXMotionCruiseVelocity_2 = 176,  // float
    kMAXMotionMaxAccel_2 = 177,  // float
    kMAXMotionMaxJerk_2 = 178,  // float
    kMAXMotionAllowedProfileError_2 = 179,  // float
    kMAXMotionPositionMode_2 = 180,  // uint32_t
    kMAXMotionCruiseVelocity_3 = 181,  // float
    kMAXMotionMaxAccel_3 = 182,  // float
    kMAXMotionMaxJerk_3 = 183,  // float
    kMAXMotionAllowedProfileError_3 = 184,  // float
    kMAXMotionPositionMode_3 = 185,  // uint32_t
    kForceEnableStatus_0 = 186,  // bool
    kForceEnableStatus_1 = 187,  // bool
    kForceEnableStatus_2 = 188,  // bool
    kForceEnableStatus_3 = 189,  // bool
    kForceEnableStatus_4 = 190,  // bool
    kForceEnableStatus_5 = 191,  // bool
    kForceEnableStatus_6 = 192,  // bool
    kForceEnableStatus_7 = 193,  // bool
    kFollowerModeLeaderId = 194,  // uint32_t
    kFollowerModeIsInverted = 195,  // bool
    kDutyCycleEncoderStartPulseUs = 196,  // float
    kDutyCycleEncoderEndPulseUs = 197,  // float
    kParamTableVersion = 198,  // uint32_t
    kStatus8Period = 199,  // uint32_t
    kForceEnableStatus_8 = 200,  // bool
    kLimitSwitchPositionSensor = 201,  // uint32_t
    kLimitSwitchFwdPosition = 202,  // float
    kLimitSwitchRevPosition = 203,  // float
    kS_0 = 204,  // float
    kA_0 = 205,  // float
    kG_0 = 206,  // float
    kCos_0 = 207,  // float
    kCosRatio_0 = 208,  // float
    kS_1 = 209,  // float
    kA_1 = 210,  // float
    kG_1 = 211,  // float
    kCos_1 = 212,  // float
    kCosRatio_1 = 213,  // float
    kS_2 = 214,  // float
    kA_2 = 215,  // float
    kG_2 = 216,  // float
    kCos_2 = 217,  // float
    kCosRatio_2 = 218,  // float
    kS_3 = 219,  // float
    kA_3 = 220,  // float
    kG_3 = 221,  // float
    kCos_3 = 222,  // float
    kCosRatio_3 = 223,  // float
    kStatus9Period = 224,  // uint32_t
    kForceEnableStatus_9 = 225,  // bool
    kDetachedEncoderDeviceID = 226,  // uint32_t
};

}  // namespace rev::spark
