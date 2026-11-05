package app.celcam

import android.content.Context
import android.util.AttributeSet
import android.view.TextureView

/** Vista previa que respeta la forma de la imagen en vez de estirarla. */
class AspectTextureView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : TextureView(context, attrs) {
    private var ratioWidth = 0
    private var ratioHeight = 0

    fun setAspect(width: Int, height: Int) {
        if (width != ratioWidth || height != ratioHeight) {
            ratioWidth = width
            ratioHeight = height
            requestLayout()
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        if (ratioWidth == 0 || ratioHeight == 0) return
        val width = measuredWidth
        val height = measuredHeight
        if (width * ratioHeight < height * ratioWidth) {
            setMeasuredDimension(width, width * ratioHeight / ratioWidth)
        } else {
            setMeasuredDimension(height * ratioWidth / ratioHeight, height)
        }
    }
}
