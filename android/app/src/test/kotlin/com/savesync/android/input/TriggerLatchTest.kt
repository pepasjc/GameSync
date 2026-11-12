package com.savesync.android.input

import org.junit.Assert.assertEquals
import org.junit.Test

class TriggerLatchTest {

    private fun fires(latch: TriggerLatch, events: List<(TriggerLatch) -> Boolean>): Int =
        events.count { it(latch) }

    @Test
    fun `analog jitter around the threshold fires once`() {
        val latch = TriggerLatch()
        var t = 0L
        val samples = listOf(0f, 0.3f, 0.61f, 0.55f, 0.62f, 0.4f, 0.65f, 1f, 0.9f, 0.3f, 0.26f)
        assertEquals(1, fires(latch, samples.map { v -> { l: TriggerLatch -> l.onAnalog(v, t++ * 10) } }))
    }

    @Test
    fun `digital key plus analog axis for one pull fires once`() {
        val latch = TriggerLatch()
        assertEquals(true, latch.onDigital(true, 0))
        assertEquals(false, latch.onAnalog(0.8f, 5))
        assertEquals(false, latch.onDigital(true, 30))   // auto-repeat
        assertEquals(false, latch.onDigital(false, 100))
        assertEquals(false, latch.onAnalog(1f, 105))     // axis still held
        assertEquals(false, latch.onAnalog(0.1f, 120))
    }

    @Test
    fun `release lagging between sources does not re-fire`() {
        val latch = TriggerLatch()
        assertEquals(true, latch.onAnalog(1f, 0))
        assertEquals(false, latch.onAnalog(0f, 40))
        assertEquals(false, latch.onDigital(true, 50))    // late digital DOWN of the same pull
        assertEquals(false, latch.onDigital(false, 60))
    }

    @Test
    fun `separate pulls each fire`() {
        val latch = TriggerLatch()
        assertEquals(true, latch.onAnalog(1f, 0))
        assertEquals(false, latch.onAnalog(0f, 100))
        assertEquals(true, latch.onAnalog(1f, 300))
        assertEquals(false, latch.onAnalog(0.1f, 400))
        assertEquals(true, latch.onDigital(true, 600))
    }
}
