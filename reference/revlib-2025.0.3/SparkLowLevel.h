/*
 * Copyright (c) 2018-2024 REV Robotics
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

#include <stdint.h>

#include <string>
#include <vector>

#include <frc/motorcontrol/MotorController.h>
#include <hal/Types.h>
#include <wpi/deprecated.h>

#include "rev/REVLibError.h"

// Defined in HIL tester source
class ConfigBase;

namespace rev::spark {

class SparkBase;

class SparkLowLevel : public frc::MotorController {
    friend class SparkBase;
    friend class SparkMax;
    friend class SparkFlex;
    friend class SparkAnalogSensor;
    friend class SparkMaxAlternateEncoder;
    friend class SparkLimitSwitch;
    friend class SparkClosedLoopController;
    friend class SparkRelativeEncoder;
    friend class SparkAbsoluteEncoder;
    friend class SparkFlexExternalEncoder;
    friend class SparkAbsoluteEncoderSim;
    friend class SparkAnalogSensorSim;
    friend class SparkExternalEncoderSim;
    friend class SparkLimitSwitchSim;
    friend class SparkMaxAlternateEncoderSim;
    friend class SparkRelativeEncoderSim;
    friend class SparkSimFaultManager;

    // Defined in HIL tester source
    friend class ::ConfigBase;

public:
    static const uint8_t kAPIMajorVersion;
    static const uint8_t kAPIMinorVersion;
    static const uint8_t kAPIBuildVersion;
    static const uint32_t kAPIVersion;

    enum class MotorType { kBrushed = 0, kBrushless = 1 };

    enum class ControlType {
        kDutyCycle = 0,
        kVelocity = 1,
        kVoltage = 2,
        kPosition = 3,
        /**
         * @deprecated It is recommended to migrate to MAXMotion Velocity mode
         * instead.
         */
        kSmartMotion
        [[deprecated("We recommend migrating to MAXMotion instead.")]] = 4,
        kCurrent = 5,
        /**
         * @deprecated It is recommended to migrate to MAXMotion Velocity mode
         * instead.
         */
        kSmartVelocity
        [[deprecated("We recommend migrating to MAXMotion instead.")]] = 6,
        kMAXMotionPositionControl = 7,
        kMAXMotionVelocityControl = 8,
    };

    enum class ParameterStatus {
        kOK = 0,
        kInvalidID = 1,
        kMismatchType = 2,
        kAccessMode = 3,
        kInvalid = 4,
        kNotImplementedDeprecated = 5,
    };

    enum class PeriodicFrame {
        kStatus0 = 0,
        kStatus1 = 1,
        kStatus2 = 2,
        kStatus3 = 3,
        kStatus4 = 4,
        kStatus5 = 5,
        kStatus6 = 6,
        kStatus7 = 7,
    };

    struct PeriodicStatus0 {
        double appliedOutput;
        double voltage;
        double current;
        uint8_t motorTemperature;
        bool hardForwardLimitReached;
        bool hardReverseLimitReached;
        bool softForwardLimitReached;
        bool softReverseLimitReached;
        bool inverted;
        bool primaryHeartbeatLock;
        uint64_t timestamp;
    };

    struct PeriodicStatus1 {
        bool otherFault;
        bool motorTypeFault;
        bool sensorFault;
        bool canFault;
        bool temperatureFault;
        bool drvFault;
        bool escEepromFault;
        bool firmwareFault;
        bool brownoutWarning;
        bool overcurrentWarning;
        bool escEepromWarning;
        bool extEepromWarning;
        bool sensorWarning;
        bool stallWarning;
        bool hasResetWarning;
        bool otherWarning;
        bool otherStickyFault;
        bool motorTypeStickyFault;
        bool sensorStickyFault;
        bool canStickyFault;
        bool temperatureStickyFault;
        bool drvStickyFault;
        bool escEepromStickyFault;
        bool firmwareStickyFault;
        bool brownoutStickyWarning;
        bool overcurrentStickyWarning;
        bool escEepromStickyWarning;
        bool extEepromStickyWarning;
        bool sensorStickyWarning;
        bool stallStickyWarning;
        bool hasResetStickyWarning;
        bool otherStickyWarning;
        bool isFollower;
        uint64_t timestamp;
    };

    struct PeriodicStatus2 {
        double primaryEncoderVelocity;
        double primaryEncoderPosition;
        uint64_t timestamp;
    };

    struct PeriodicStatus3 {
        double analogVoltage;
        double analogVelocity;
        double analogPosition;
        uint64_t timestamp;
    };

    struct PeriodicStatus4 {
        double externalOrAltEncoderVelocity;
        double externalOrAltEncoderPosition;
        uint64_t timestamp;
    };

    struct PeriodicStatus5 {
        double dutyCycleEncoderVelocity;
        double dutyCycleEncoderPosition;
        uint64_t timestamp;
    };

    struct PeriodicStatus6 {
        double unadjustedDutyCycle;
        double dutyCyclePeriod;
        uint8_t dutyCycleNoSignal;
        uint64_t timestamp;
    };

    struct PeriodicStatus7 {
        double iAccumulation;
        uint64_t timestamp;
    };

    enum class SparkModel {
        kSparkMax = 0,
        kSparkFlex = 1,
        kUnknown = 255,
    };

    /**
     * Closes the SPARK motor controller
     */
    virtual ~SparkLowLevel();

    /**
     * Get the firmware version of the SPARK.
     *
     * @return uint32_t Firmware version integer. Value is represented as 4
     * bytes, Major.Minor.Build H.Build L
     *
     */
    uint32_t GetFirmwareVersion();

    uint32_t GetFirmwareVersion(bool& isDebugBuild);

    /**
     * Get the firmware version of the SPARK as a string.
     *
     * @return std::string Human readable firmware version string
     *
     */
    std::string GetFirmwareString();

    /**
     * Get the unique serial number of the SPARK. Currently not implemented.
     *
     * @return std::vector<uint8_t> Vector of bytes representig the unique
     * serial number
     *
     */
    std::vector<uint8_t> GetSerialNumber();

    /**
     * Get the configured Device ID of the SPARK.
     *
     * @return int device ID
     *
     */
    int GetDeviceId() const;

    /**
     * Get the motor type setting for the SPARK.
     *
     * @return MotorType Motor type setting
     *
     */
    MotorType GetMotorType();

    /**
     * Set the amount of time to wait for a periodic status frame before
     * returning a timeout error. This timeout will apply to all periodic status
     * frames for the SPARK motor controller.
     *
     * To prevent invalid timeout errors, the minimum timeout for a given
     * periodic status is 2.1 times its period. To use the minimum timeout for
     * all status frames, set timeoutMs to 0.
     *
     * The default timeout is 500ms.
     *
     * @param timeoutMs The timeout in milliseconds
     */
    void SetPeriodicFrameTimeout(int timeoutMs);

    /**
     * Set the maximum number of times to retry an RTR CAN frame. This applies
     * to calls such as SetParameter* and GetParameter* where a request is made
     * to the SPARK motor controller and a response is expected. Anytime sending
     * the request or receiving the response fails, it will retry the request a
     * number of times, no more than the value set by this method. If an attempt
     * succeeds, it will immediately return. The minimum number of retries is 0,
     * where only a single attempt will be made and will return regardless of
     * success or failure.
     *
     * The default maximum is 5 retries.
     *
     * @param numRetries The maximum number of retries
     */
    void SetCANMaxRetries(int numRetries);

    /**
     * Set the control frame send period for the native CAN Send thread. To
     * disable periodic sends, set periodMs to 0.
     *
     * @param periodMs The send period in milliseconds between 1ms and 100ms
     * or set to 0 to disable periodic sends. Note this is not updated until
     * the next call to Set() or SetReference().
     *
     */
    void SetControlFramePeriodMs(int periodMs);

    /**
     * Create the sim gui Fault Manager for this Spark Device
     */
    void CreateSimFaultManager();

protected:
    PeriodicStatus0 GetPeriodicStatus0();
    PeriodicStatus1 GetPeriodicStatus1();
    PeriodicStatus2 GetPeriodicStatus2();
    PeriodicStatus3 GetPeriodicStatus3();
    PeriodicStatus4 GetPeriodicStatus4();
    PeriodicStatus5 GetPeriodicStatus5();
    PeriodicStatus6 GetPeriodicStatus6();
    PeriodicStatus7 GetPeriodicStatus7();

    REVLibError SetpointCommand(
        double value, SparkLowLevel::ControlType ctrl = ControlType::kDutyCycle,
        int pidSlot = 0, double arbFeedforward = 0, int arbFFUnits = 0);

    float GetSafeFloat(float f);

    MotorType m_motorType;
    SparkModel m_expectedSparkModel;
    // The type is void* because we don't want to expose c_Spark_handle to
    // the consumers of this header file
    void* m_sparkMaxHandle;

private:
    explicit SparkLowLevel(int deviceID, MotorType type, SparkModel model);

    int m_deviceID;
};

}  // namespace rev::spark
