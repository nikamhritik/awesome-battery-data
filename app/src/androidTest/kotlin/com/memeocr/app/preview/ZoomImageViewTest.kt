package com.memeocr.app.preview

import android.graphics.Bitmap
import android.os.SystemClock
import android.view.MotionEvent
import android.view.ViewConfiguration
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ZoomImageViewTest {
    private fun withView(block: (ZoomImageView) -> Unit) {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync {
            val bitmap = Bitmap.createBitmap(800, 400, Bitmap.Config.ARGB_8888)
            val view = ZoomImageView(instrumentation.targetContext)
            view.layout(0, 0, 400, 400)
            view.setBitmap(bitmap)
            try { block(view) } finally { view.setBitmap(null); bitmap.recycle() }
        }
    }
    @Test fun zoomHasUpperAndLowerBounds() = withView {
        it.scaleBy(100f, 200f, 200f)
        assertEquals(8f, it.zoom, 0.001f)
        it.scaleBy(0.001f, 200f, 200f)
        assertEquals(1f, it.zoom, 0.001f)
        assertEquals(Pair(0f, 0f), it.offsets())
    }
    @Test fun dragCannotLoseImageAndShortAxisStaysCentered() = withView {
        it.panBy(999f, -999f)
        assertEquals(Pair(0f, 0f), it.offsets())
        it.scaleBy(3f, 200f, 200f)
        it.panBy(9999f, -9999f)
        assertEquals(Pair(400f, -100f), it.offsets())
    }
    @Test fun zoomPreservesFocusUntilImageEdge() = withView {
        it.scaleBy(3f, 100f, 200f)
        assertEquals(Pair(200f, 0f), it.offsets())
        it.reset()
        assertEquals(1f, it.zoom, 0.001f)
        assertEquals(Pair(0f, 0f), it.offsets())
    }
    @Test fun resizeAndNewImageResetTransform() = withView {
        it.scaleBy(4f, 100f, 100f)
        it.layout(0, 0, 600, 300)
        assertEquals(1f, it.zoom, 0.001f)
        it.scaleBy(4f, 100f, 100f)
        it.setBitmap(null)
        assertEquals(1f, it.zoom, 0.001f)
        it.scaleBy(4f, 100f, 100f)
        assertEquals(1f, it.zoom, 0.001f)
    }
    @Test fun invalidScaleIsIgnored() = withView {
        for (factor in listOf(Float.NaN, Float.POSITIVE_INFINITY, 0f, -1f))
            it.scaleBy(factor, 100f, 100f)
        assertEquals(1f, it.zoom, 0.001f)
    }
    @Test fun confirmedSingleTapClicksButDoubleTapOnlyZooms() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        for (doubleTap in listOf(false, true)) {
            val clicks = AtomicInteger()
            val settled = CountDownLatch(1)
            lateinit var view: ZoomImageView
            lateinit var bitmap: Bitmap
            instrumentation.runOnMainSync {
                bitmap = Bitmap.createBitmap(800, 400, Bitmap.Config.ARGB_8888)
                view = ZoomImageView(instrumentation.targetContext)
                view.layout(0, 0, 400, 400)
                view.setBitmap(bitmap)
                view.setOnClickListener { clicks.incrementAndGet() }
                val start = SystemClock.uptimeMillis()
                fun send(delay: Long, action: Int, down: Long) {
                    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                        val event = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, 200f, 200f, 0)
                        view.onTouchEvent(event)
                        event.recycle()
                    }, delay)
                }
                send(0, MotionEvent.ACTION_DOWN, start)
                send(30, MotionEvent.ACTION_UP, start)
                if (doubleTap) {
                    send(100, MotionEvent.ACTION_DOWN, start + 100)
                    send(130, MotionEvent.ACTION_UP, start + 100)
                }
                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                    settled.countDown()
                }, ViewConfiguration.getDoubleTapTimeout().toLong() + 300)
            }
            try {
                assertTrue(settled.await(5, TimeUnit.SECONDS))
                instrumentation.runOnMainSync {
                    assertEquals(if (doubleTap) 0 else 1, clicks.get())
                    assertEquals(if (doubleTap) 3f else 1f, view.zoom, 0.001f)
                }
            } finally {
                instrumentation.runOnMainSync { view.setBitmap(null); bitmap.recycle() }
            }
        }
    }
    @Test fun doubleTapMagnifiesAndSecondDoubleTapResets() = withView { view ->
        var time = SystemClock.uptimeMillis()
        fun tap() {
            val down = time
            for (action in listOf(MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP)) {
                val event = MotionEvent.obtain(down, time, action, 200f, 200f, 0)
                view.onTouchEvent(event); event.recycle(); time += 60
            }
        }
        tap(); tap()
        assertEquals(3f, view.zoom, 0.001f)
        time += 500
        tap(); tap()
        assertEquals(1f, view.zoom, 0.001f)
    }
}
