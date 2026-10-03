package com.memeocr.core

/** Selection belongs to one result set and follows its order, without loading images. */
class Selection {
    private val available = linkedSetOf<String>()
    private val selected = mutableSetOf<String>()

    val size: Int get() = selected.size
    fun contains(key: String): Boolean = key in selected

    fun replaceResults(keys: Iterable<String>) {
        available.clear()
        available.addAll(keys)
        selected.clear()
    }

    fun setSelected(key: String, value: Boolean) {
        if (key !in available) return
        if (value) selected.add(key) else selected.remove(key)
    }

    fun toggle(key: String) = setSelected(key, !contains(key))
    fun selectAll() { selected.addAll(available) }
    fun clear() { selected.clear() }
    fun selectedKeys(): List<String> = available.filter { it in selected }
}
