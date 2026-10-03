package com.memeocr.app.share

import android.content.ClipData
import android.content.Intent
import android.net.Uri

/** Original MediaStore images, with temporary read access for every attachment. */
object ShareIntents {
    fun images(values: List<Uri>): Intent {
        val uris = values.distinct()
        require(uris.isNotEmpty()) { "请选择图片" }
        require(uris.all { it.scheme == "content" }) { "图片需要本地媒体 URI" }
        return Intent(if (uris.size == 1) Intent.ACTION_SEND else Intent.ACTION_SEND_MULTIPLE).apply {
            type = "image/*"
            if (uris.size == 1) putExtra(Intent.EXTRA_STREAM, uris.first())
            else putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
            clipData = ClipData("meme", arrayOf("image/*"), ClipData.Item(uris.first())).apply {
                uris.drop(1).forEach { addItem(ClipData.Item(it)) }
            }
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }
}
