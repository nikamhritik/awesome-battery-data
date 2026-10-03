package com.memeocr.core

import org.junit.Assert.*
import org.junit.Test
import kotlin.random.Random

class SelectionTest {
    @Test fun emptyAndUnknownKeysCannotBeSelected() {
        val selection = Selection()
        selection.toggle("missing")
        selection.selectAll()
        assertEquals(0, selection.size)
        assertEquals(emptyList<String>(), selection.selectedKeys())
        selection.replaceResults(listOf("a"))
        selection.setSelected("missing", true)
        assertFalse(selection.contains("missing"))
    }

    @Test fun selectionsFollowResultOrderAfterTogglingAndReselecting() {
        val selection = Selection()
        selection.replaceResults(listOf("a", "b", "c", "a"))
        selection.toggle("c")
        selection.toggle("a")
        assertEquals(listOf("a", "c"), selection.selectedKeys())
        selection.toggle("a")
        assertEquals(listOf("c"), selection.selectedKeys())
        selection.toggle("a")
        selection.selectAll()
        selection.selectAll()
        assertEquals(3, selection.size)
        assertEquals(listOf("a", "b", "c"), selection.selectedKeys())
    }

    @Test fun replacingResultsClearsSelectionEvenForSameLocationWithNewVersion() {
        val selection = Selection()
        val original = MediaItem("content://image/1", 5, 6).versionKey
        val changed = MediaItem("content://image/1", 7, 6).versionKey
        selection.replaceResults(listOf(original))
        selection.selectAll()
        selection.replaceResults(listOf(changed))
        selection.setSelected(original, true)
        assertEquals(0, selection.size)
        selection.toggle(changed)
        assertEquals(listOf(changed), selection.selectedKeys())
        selection.replaceResults(emptyList())
        assertEquals(0, selection.size)
    }

    @Test fun clearingCanBeFollowedBySelectingAgain() {
        val selection = Selection()
        selection.replaceResults(listOf("a", "b"))
        selection.selectAll()
        val snapshot = selection.selectedKeys()
        selection.clear()
        assertEquals(0, selection.size)
        assertEquals(listOf("a", "b"), snapshot)
        selection.toggle("b")
        assertEquals(listOf("b"), selection.selectedKeys())
    }

    @Test fun nineThousandResultsRemainCorrectAcrossPagesAndRepeatedOperations() {
        val keys = (0 until 9000).map { "content://image/$it|$it|123" }
        val selection = Selection()
        selection.replaceResults(keys + keys.take(48))
        selection.toggle(keys[8999])
        selection.toggle(keys[0])
        selection.toggle(keys[48])
        assertEquals(listOf(keys[0], keys[48], keys[8999]), selection.selectedKeys())
        selection.selectAll()
        assertEquals(9000, selection.size)
        val expected = keys.toMutableSet()
        val random = Random(5840)
        repeat(2000) {
            val key = keys[random.nextInt(keys.size)]
            selection.toggle(key)
            if (!expected.remove(key)) expected.add(key)
        }
        assertEquals(expected.size, selection.size)
        assertEquals(keys.filter { it in expected }, selection.selectedKeys())
        selection.clear()
        assertEquals(0, selection.size)
    }
}
