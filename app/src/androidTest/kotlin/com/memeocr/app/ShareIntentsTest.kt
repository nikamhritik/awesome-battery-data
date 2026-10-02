package com.memeocr.app

import android.content.Intent
import android.net.Uri
import android.os.Parcel
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memeocr.app.share.ShareIntents
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ShareIntentsTest {
    private fun uri(id: Int) = Uri.parse("content://media/external/images/media/$id")

    @Suppress("DEPRECATION")
    @Test fun oneImageKeepsSingleShareAndReadOnlyGrant() {
        val intent = ShareIntents.images(listOf(uri(1), uri(1)))
        assertEquals(Intent.ACTION_SEND, intent.action)
        assertEquals("image/*", intent.type)
        assertEquals(uri(1), intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
        assertEquals(1, intent.clipData!!.itemCount)
        assertEquals(uri(1), intent.clipData!!.getItemAt(0).uri)
        assertEquals(Intent.FLAG_GRANT_READ_URI_PERMISSION, intent.flags)
    }

    @Suppress("DEPRECATION")
    @Test fun multipleImagesKeepEveryUriAndPermissionAfterParcelRoundTrip() {
        val values = listOf(uri(8), uri(2), uri(5))
        val original = ShareIntents.images(values + uri(2))
        val parcel = Parcel.obtain()
        try {
            original.writeToParcel(parcel, 0)
            parcel.setDataPosition(0)
            val intent = Intent.CREATOR.createFromParcel(parcel)
            assertEquals(Intent.ACTION_SEND_MULTIPLE, intent.action)
            assertEquals(values, intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM))
            assertEquals(values, (0 until intent.clipData!!.itemCount).map {
                intent.clipData!!.getItemAt(it).uri
            })
            assertEquals(Intent.FLAG_GRANT_READ_URI_PERMISSION, intent.flags)
            assertEquals(0, intent.flags and Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            assertEquals(0, intent.flags and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        } finally { parcel.recycle() }
    }

    @Test(expected = IllegalArgumentException::class)
    fun emptySelectionCannotOpenSharesheet() { ShareIntents.images(emptyList()) }

    @Test(expected = IllegalArgumentException::class)
    fun fileUrisAreRejected() { ShareIntents.images(listOf(Uri.parse("file:///example.png"))) }
}
