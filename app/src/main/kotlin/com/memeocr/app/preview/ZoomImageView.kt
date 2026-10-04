package com.memeocr.app.preview

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import kotlin.math.min

/** A single bounded bitmap; gestures never decode or write the source image. */
class ZoomImageView(context: Context) : View(context) {
    private var bitmap: Bitmap? = null
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    internal var zoom = 1f
        private set
    private var offsetX = 0f
    private var offsetY = 0f
    private val fit: Float get() = bitmap?.let {
        min(width.toFloat() / it.width, height.toFloat() / it.height)
    } ?: 1f
    private val scaler = ScaleGestureDetector(context,
        object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                scaleBy(detector.scaleFactor, detector.focusX, detector.focusY)
                return true
            }
        })
    private val gestures = GestureDetector(context,
        object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent) = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean = performClick()
            override fun onDoubleTap(e: MotionEvent): Boolean {
                if (zoom > 1.01f) reset() else scaleBy(3f, e.x, e.y)
                return true
            }
            override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
                if (!scaler.isInProgress && e2.pointerCount == 1) panBy(-dx, -dy)
                return true
            }
        })

    init {
        setBackgroundColor(Color.BLACK)
        contentDescription = "放大图片，单击退出，双指缩放，拖动查看，双击放大或复位"
        isClickable = true
    }

    fun setBitmap(value: Bitmap?) { bitmap = value; reset() }
    internal fun reset() { zoom = 1f; offsetX = 0f; offsetY = 0f; invalidate() }
    internal fun scaleBy(factor: Float, focusX: Float, focusY: Float) {
        if (bitmap == null || !factor.isFinite() || factor <= 0f) return
        val next = (zoom * factor).coerceIn(1f, 8f)
        val ratio = next / zoom
        offsetX = (offsetX + width / 2f - focusX) * ratio + focusX - width / 2f
        offsetY = (offsetY + height / 2f - focusY) * ratio + focusY - height / 2f
        zoom = next
        constrain()
    }
    internal fun panBy(dx: Float, dy: Float) {
        offsetX += dx; offsetY += dy; constrain()
    }
    private fun constrain() {
        bitmap?.let {
            val limitX = ((it.width * fit * zoom - width) / 2f).coerceAtLeast(0f)
            val limitY = ((it.height * fit * zoom - height) / 2f).coerceAtLeast(0f)
            offsetX = offsetX.coerceIn(-limitX, limitX)
            offsetY = offsetY.coerceIn(-limitY, limitY)
        }
        invalidate()
    }
    internal fun offsets() = Pair(offsetX, offsetY)
    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) { reset() }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        bitmap?.let {
            val scale = fit * zoom
            canvas.save()
            canvas.translate(width / 2f + offsetX, height / 2f + offsetY)
            canvas.scale(scale, scale)
            canvas.drawBitmap(it, -it.width / 2f, -it.height / 2f, paint)
            canvas.restore()
        }
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        scaler.onTouchEvent(event)
        gestures.onTouchEvent(event)
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}
