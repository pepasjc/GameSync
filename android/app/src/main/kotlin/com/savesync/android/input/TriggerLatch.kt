package com.savesync.android.input

/**
 * One L2 or R2 trigger, turned into exactly one "pressed" event per pull.
 *
 * Many pads report a trigger twice — a digital `KEYCODE_BUTTON_L2/R2` key
 * *and* an analog axis — and an analog trigger jitters around any single
 * threshold as it travels.  Both made one pull step the system filter
 * several times.  So:
 *
 * - the digital key and the analog axis feed one pressed state, and only
 *   its up → down edge fires;
 * - the axis uses hysteresis: it counts as pressed at [pressAt] and only as
 *   released again below [releaseAt];
 * - an edge within [minIntervalMs] of the last fire is ignored, which
 *   absorbs a source that reports release a moment before the other.
 */
class TriggerLatch(
    private val pressAt: Float = 0.6f,
    private val releaseAt: Float = 0.25f,
    private val minIntervalMs: Long = 150L,
) {
    private var digitalDown = false
    private var analogDown = false
    private var pressed = false
    private var lastFireAt = Long.MIN_VALUE / 2

    /** Digital key DOWN (incl. auto-repeat) / UP.  True when this press should act. */
    fun onDigital(down: Boolean, nowMs: Long): Boolean {
        digitalDown = down
        return update(nowMs)
    }

    /** Analog axis sample in 0..1.  True when this press should act. */
    fun onAnalog(value: Float, nowMs: Long): Boolean {
        analogDown = if (analogDown) value > releaseAt else value >= pressAt
        return update(nowMs)
    }

    private fun update(nowMs: Long): Boolean {
        val down = digitalDown || analogDown
        if (!down) {
            pressed = false
            return false
        }
        if (pressed) return false
        pressed = true
        if (nowMs - lastFireAt < minIntervalMs) return false
        lastFireAt = nowMs
        return true
    }
}
