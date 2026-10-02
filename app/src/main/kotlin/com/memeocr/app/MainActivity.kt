package com.memeocr.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.*
import android.provider.Settings
import android.view.*
import android.view.inputmethod.InputMethodManager
import android.widget.*
import com.memeocr.app.data.RecognitionDb
import com.memeocr.app.media.ImageLoader
import com.memeocr.app.media.MediaStoreReader
import com.memeocr.app.ocr.OcrService
import com.memeocr.app.share.ShareIntents
import com.memeocr.core.*
import java.util.concurrent.*

/** Native Android UI; all storage, search and image work stays off the main thread. */
class MainActivity : Activity() {
    private val ink = Color.rgb(28, 47, 49)
    private val teal = Color.rgb(18, 106, 100)
    private val paper = Color.rgb(247, 249, 246)
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private val thumbs = ThreadPoolExecutor(2, 2, 30, TimeUnit.SECONDS,
        ArrayBlockingQueue<Runnable>(96), ThreadPoolExecutor.DiscardOldestPolicy())
    private lateinit var db: RecognitionDb
    private lateinit var reader: MediaStoreReader
    private lateinit var root: LinearLayout
    private lateinit var albumPage: ScrollView
    private lateinit var searchPage: LinearLayout
    private lateinit var permissionPage: LinearLayout
    private lateinit var albumSpinner: Spinner
    private lateinit var batchInput: EditText
    private lateinit var searchInput: EditText
    private lateinit var regex: CheckBox
    private lateinit var start: Button
    private lateinit var retry: Button
    private lateinit var summary: TextView
    private lateinit var progressText: TextView
    private lateinit var permissionNote: TextView
    private lateinit var resultText: TextView
    private lateinit var bar: ProgressBar
    private lateinit var grid: GridView
    private lateinit var adapter: HitAdapter
    private lateinit var bulkButton: Button
    private lateinit var bulkActions: LinearLayout
    private lateinit var selectAllButton: Button
    private lateinit var clearButton: Button
    private lateinit var shareButton: Button
    private lateinit var selectionText: TextView
    private val selection = Selection()
    private var bulkMode = false
    private var sharing = false
    @Volatile private var shareGeneration = 0
    private var hasSearched = false
    private var resultQuery: String? = null
    private var resultRegex = false
    private var restoredKeys: List<String>? = null
    private var refreshingKeys = emptyList<String>()
    private data class RetainedSelection(val keys: List<String>)
    private var albums = emptyList<Album>()
    private var selected: Album? = null
    private var searchVisible = false
    private var searchGeneration = 0
    private var albumGeneration = 0
    private var previousProgress: ProgressState? = null
    private var disposed = false
    private val prefs by lazy { getSharedPreferences("ui", MODE_PRIVATE) }

    private val poll = object : Runnable {
        override fun run() {
            if (disposed) return
            val state = OcrService.liveProgress
            if (state != previousProgress) {
                previousProgress = state
                renderProgress(state)
                if (!state.running && hasPhotos()) refreshStats()
            }
            main.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        db = RecognitionDb(this)
        reader = MediaStoreReader(this)
        buildUi()
        savedInstanceState?.let {
            searchInput.setText(it.getString("query", ""))
            regex.isChecked = it.getBoolean("regex")
            searchVisible = it.getBoolean("searchVisible")
            hasSearched = it.getBoolean("hasSearched")
            resultQuery = it.getString("resultQuery")
            resultRegex = it.getBoolean("resultRegex")
            bulkMode = it.getBoolean("bulkMode")
        }
        restoredKeys = (lastNonConfigurationInstance as? RetainedSelection)?.keys
        updateSelection()
        setContentView(root)
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 35) {
                val padding = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(dp(16), padding.top, dp(16), padding.bottom)
            }
            insets
        }
    }

    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()
    private fun text(value: String, size: Float = 14f) = TextView(this).apply {
        text = value; textSize = size; setTextColor(ink)
        setPadding(0, dp(6), 0, dp(6))
    }
    private fun button(label: String, action: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; minHeight = dp(48)
        setTextColor(teal)
        setOnClickListener { action() }
    }
    private fun column() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun LinearLayout.full(view: View) {
        addView(view, LinearLayout.LayoutParams(-1, -2))
    }

    private fun buildUi() {
        root = column().apply { setBackgroundColor(paper); setPadding(dp(16), 0, dp(16), 0) }
        root.full(text("Meme 文字搜索", 25f).apply {
            setTypeface(typeface, Typeface.BOLD); setPadding(0, dp(20), 0, dp(2))
        })
        root.full(text("在自己的相册里，找回那张图", 13f))
        val tabs = LinearLayout(this)
        tabs.addView(button("识别相册") { showPage(false) }, LinearLayout.LayoutParams(0, -2, 1f))
        tabs.addView(button("搜索图片") { showPage(true) }, LinearLayout.LayoutParams(0, -2, 1f))
        root.full(tabs)

        val albumContent = column()
        permissionNote = text("")
        albumContent.full(permissionNote)
        albumContent.full(text("1  选择本地相册", 18f))
        albumSpinner = Spinner(this).apply { minimumHeight = dp(52); contentDescription = "选择相册" }
        albumSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?) {}
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                selected = albums.getOrNull(position)
                prefs.edit().putString("bucket", selected?.bucketId).apply()
                refreshStats()
            }
        }
        albumContent.full(albumSpinner)
        albumContent.full(button("刷新相册 / 调整照片授权") { requestPhotoPermission() })
        summary = text("选择相册后查看进度")
        albumContent.full(summary)
        albumContent.full(text("2  分批识别", 18f))
        albumContent.full(text("本批最多处理多少张（1–10000）"))
        batchInput = EditText(this).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            setSingleLine(); contentDescription = "本批数量"
            setText(prefs.getInt("batch", 1000).toString()); minHeight = dp(48)
        }
        albumContent.full(batchInput)
        start = button("开始识别本批") { startBatch(false) }.apply {
            setTextColor(Color.WHITE); backgroundTintList = ColorStateList.valueOf(teal)
        }
        retry = button("重试本相册失败项") { startBatch(true) }
        albumContent.full(start); albumContent.full(retry)
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            progressTintList = ColorStateList.valueOf(teal); visibility = View.GONE
        }
        albumContent.full(bar)
        progressText = text("每张完成后保存；下次自动跳过已完成的图片。")
        albumContent.full(progressText)
        albumContent.full(text("原图只读 · 离线识别 · 动图仅识别首帧\n小字或艺术字可能漏识别。退出后已保存的结果保留。", 12f))
        albumPage = ScrollView(this).apply { addView(albumContent) }

        searchPage = column()
        searchPage.full(text("查找已识别的图片", 18f))
        searchPage.full(text("搜索所有已识别相册；普通搜索忽略大小写和全角差异。", 12f))
        searchInput = EditText(this).apply {
            hint = "输入图片里的文字"; contentDescription = "搜索文字"
            setSingleLine(); minHeight = dp(48)
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH
            setOnEditorActionListener { _, _, _ -> search(); true }
        }
        searchPage.full(searchInput)
        regex = CheckBox(this).apply {
            text = "正则模式"; minHeight = dp(48)
            setPadding(dp(4), 0, dp(4), 0)
        }
        searchPage.full(regex)
        searchPage.full(text("正则匹配原文、区分大小写；不支持前后查找和反向引用。", 12f))
        searchPage.full(button("查找图片") { search() })
        resultText = text("识别完成后，输入文字查找。")
        searchPage.full(resultText)
        val selectionRow = LinearLayout(this)
        bulkButton = button("批量选择") {
            bulkMode = !bulkMode
            if (!bulkMode) selection.clear()
            updateSelection(); adapter.notifyDataSetChanged()
        }
        selectionText = text("已选 0 张")
        selectionRow.addView(bulkButton, LinearLayout.LayoutParams(0, -2, 1f))
        selectionRow.addView(selectionText, LinearLayout.LayoutParams(0, -2, 1f))
        searchPage.full(selectionRow)
        bulkActions = LinearLayout(this)
        selectAllButton = button("全选结果") {
            selection.selectAll(); updateSelection(); adapter.notifyDataSetChanged()
        }
        clearButton = button("清空选择") {
            selection.clear(); updateSelection(); adapter.notifyDataSetChanged()
        }
        shareButton = button("分享所选") {
            share(adapter.hits.filter { selection.contains(key(it.record)) }.map { it.record })
        }
        listOf(selectAllButton, clearButton, shareButton).forEach {
            bulkActions.addView(it, LinearLayout.LayoutParams(0, -2, 1f))
        }
        searchPage.full(bulkActions)
        adapter = HitAdapter()
        grid = GridView(this).apply {
            numColumns = GridView.AUTO_FIT; columnWidth = dp(105)
            stretchMode = GridView.STRETCH_COLUMN_WIDTH
            verticalSpacing = dp(8); horizontalSpacing = dp(8)
            adapter = this@MainActivity.adapter
            setOnItemClickListener { _, _, position, _ ->
                val hit = this@MainActivity.adapter.hits.getOrNull(position) ?: return@setOnItemClickListener
                if (bulkMode) {
                    selection.toggle(key(hit.record)); updateSelection()
                    this@MainActivity.adapter.notifyDataSetChanged()
                } else preview(hit)
            }
            setOnItemLongClickListener { _, _, position, _ ->
                val hit = this@MainActivity.adapter.hits.getOrNull(position) ?: return@setOnItemLongClickListener false
                bulkMode = true; selection.setSelected(key(hit.record), true)
                updateSelection(); this@MainActivity.adapter.notifyDataSetChanged(); true
            }
        }
        searchPage.addView(grid, LinearLayout.LayoutParams(-1, 0, 1f))

        permissionPage = column().apply {
            gravity = Gravity.CENTER
            full(text("允许读取照片后开始", 20f))
            full(text("应用只读取你授权的照片。识别文字保存在本机，不修改或上传图片。"))
            full(button("授权读取照片") { requestPhotoPermission() })
            full(button("打开应用权限设置") {
                startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + packageName)))
            })
        }
        val body = FrameLayout(this).apply {
            addView(albumPage, FrameLayout.LayoutParams(-1, -1))
            addView(searchPage, FrameLayout.LayoutParams(-1, -1))
            addView(permissionPage, FrameLayout.LayoutParams(-1, -1))
        }
        root.addView(body, LinearLayout.LayoutParams(-1, 0, 1f))
        updateSelection()
    }

    private fun hasPhotos(): Boolean {
        val permission = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
                         else Manifest.permission.READ_EXTERNAL_STORAGE
        return checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED ||
            (Build.VERSION.SDK_INT >= 34 &&
             checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED) == PackageManager.PERMISSION_GRANTED)
    }

    private fun requestPhotoPermission() {
        val permissions = mutableListOf(if (Build.VERSION.SDK_INT >= 33)
            Manifest.permission.READ_MEDIA_IMAGES else Manifest.permission.READ_EXTERNAL_STORAGE)
        if (Build.VERSION.SDK_INT >= 34) permissions.add(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
        if (Build.VERSION.SDK_INT >= 33) permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        requestPermissions(permissions.toTypedArray(), 10)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, grants: IntArray) {
        super.onRequestPermissionsResult(code, permissions, grants)
        showPage(searchVisible)
        if (hasPhotos()) refreshAlbums()
    }

    private fun showPage(search: Boolean) {
        searchVisible = search
        val allowed = hasPhotos()
        permissionPage.visibility = if (allowed) View.GONE else View.VISIBLE
        albumPage.visibility = if (allowed && !search) View.VISIBLE else View.GONE
        searchPage.visibility = if (allowed && search) View.VISIBLE else View.GONE
        if (!allowed) {
            searchGeneration++; shareGeneration++; sharing = false
            selection.replaceResults(emptyList()); restoredKeys = null; refreshingKeys = emptyList(); bulkMode = false
            adapter.hits = emptyList(); adapter.notifyDataSetChanged(); updateSelection()
        } else if (!search && sharing) {
            shareGeneration++; sharing = false; updateSelection()
        }
    }

    private fun <T> read(block: () -> T, failed: ((Exception) -> Unit)? = null, done: (T) -> Unit) {
        if (disposed) return
        io.execute {
            try {
                val value = block()
                main.post { if (!disposed) done(value) }
            } catch (e: Exception) {
                main.post {
                    if (!disposed) {
                        if (failed != null) failed(e) else {
                            Toast.makeText(this, RecognitionPolicy.describeError(e), Toast.LENGTH_LONG).show()
                            if (e is SecurityException) showPage(searchVisible)
                        }
                    }
                }
            }
        }
    }

    private fun refreshAlbums() {
        val generation = ++albumGeneration
        read({ reader.listAlbums() }) { list ->
            if (generation != albumGeneration) return@read
            albums = list
            val previous = prefs.getString("bucket", null)
            albumSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
                list.map { it.name + " · " + it.imageCount + " 张" })
            val index = list.indexOfFirst { it.bucketId == previous }.coerceAtLeast(0)
            selected = list.getOrNull(index)
            albumSpinner.setSelection(index)
            val partial = Build.VERSION.SDK_INT >= 34 &&
                checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES) != PackageManager.PERMISSION_GRANTED
            permissionNote.text = (if (partial) "仅显示已授权的部分照片。 " else "") +
                "当前可读取 " + list.sumOf { it.imageCount } + " 张照片"
            if (list.isEmpty()) summary.text = "没有找到可访问的本地相册，请检查照片授权。"
            refreshStats()
        }
    }

    private fun refreshStats() {
        val album = selected ?: return
        read({
            val items = reader.listImages(album.bucketId)
            val records = db.allRecords().associateBy { it.uri }
            val valid = items.mapNotNull { item -> records[item.uri]?.takeIf { it.matchesVersion(item) } }
            val done = valid.count { it.status == Status.DONE || it.status == Status.EMPTY_TEXT }
            val failed = valid.count { it.status == Status.FAILED }
            "本相册 " + items.size + " 张 · 已完成 " + done + " 张\n待识别 " +
                (items.size - done - failed) + " 张 · 失败 " + failed + " 张（可重试）"
        }) { value -> if (selected?.bucketId == album.bucketId) summary.text = value }
    }

    private fun startBatch(retryOnly: Boolean) {
        if (OcrService.liveProgress.running) {
            startService(Intent(this, OcrService::class.java).setAction(OcrService.ACTION_CANCEL))
            start.isEnabled = false
            progressText.text = "正在停止；当前图片处理完后会保存。"
            return
        }
        val album = selected ?: run { toast("请先选择相册"); return }
        val size = batchInput.text.toString().toIntOrNull()
        if (size == null || size !in 1..10000) { toast("请输入 1–10000 之间的数量"); return }
        prefs.edit().putInt("batch", size).apply()
        try {
            startForegroundService(Intent(this, OcrService::class.java).setAction(OcrService.ACTION_START)
                .putExtra(OcrService.EXTRA_BUCKET_ID, album.bucketId)
                .putExtra(OcrService.EXTRA_BATCH_SIZE, size).putExtra(OcrService.EXTRA_RETRY, retryOnly))
            start.isEnabled = false
            main.postDelayed({ if (!disposed) start.isEnabled = true }, 800)
        } catch (e: Exception) { toast("无法启动识别：" + e.message) }
    }

    private fun renderProgress(p: ProgressState) {
        start.text = if (p.running) "停止本批" else "开始识别本批"
        if (!p.running) start.isEnabled = true
        retry.isEnabled = !p.running
        albumSpinner.isEnabled = !p.running
        batchInput.isEnabled = !p.running
        bar.visibility = if (p.running || p.batchTotal > 0) View.VISIBLE else View.GONE
        bar.isIndeterminate = p.running && p.batchTotal == 0
        bar.max = p.batchTotal.coerceAtLeast(1); bar.progress = p.processed
        progressText.text = when {
            p.running && p.batchTotal == 0 -> "正在检查相册…"
            p.batchTotal > 0 -> (if (p.running) "识别中" else if (p.cancelled) "已停止" else "本批结束") +
                " · " + p.processed + " / " + p.batchTotal +
                "\n有文字 " + p.succeeded + " · 无文字 " + p.emptyText + " · 失败 " + p.failed
            p == ProgressState.idle() -> "每张完成后保存；下次自动跳过已完成的图片。"
            else -> "没有符合本次条件的图片。"
        } + (p.lastError?.let { "\n最近提示：" + it } ?: "")
    }

    private fun search(restore: List<String> = emptyList(), restoring: Boolean = false) {
        val generation = ++searchGeneration
        refreshingKeys = restore
        shareGeneration++; sharing = false
        selection.replaceResults(emptyList())
        if (!restoring) bulkMode = false
        adapter.hits = emptyList(); adapter.notifyDataSetChanged(); updateSelection()
        val query = if (restoring) resultQuery ?: searchInput.text.toString() else searchInput.text.toString()
        val regexMode = if (restoring) resultRegex else regex.isChecked
        if (!restoring) { resultQuery = query; resultRegex = regexMode }
        if (query.isBlank()) {
            hasSearched = false; resultText.text = "请输入要找的文字。"; return
        }
        hasSearched = true
        val mode = if (regexMode) SearchMode.Regex(query) else SearchMode.Literal(query)
        if (mode is SearchMode.Regex && RegexSearch.validate(query).isFailure) {
            resultText.text = RegexSearch.validate(query).exceptionOrNull()?.message
            return
        }
        getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(searchInput.windowToken, 0)
        resultText.text = "正在查找…"
        read({
            val current = reader.listImages().associateBy { it.uri }
            val accessible = db.allRecords().filter { record ->
                current[record.uri]?.let { record.matchesVersion(it) } == true
            }
            TextSearch.search(accessible, mode)
        }, failed = { e ->
            if (generation == searchGeneration) {
                refreshingKeys = emptyList()
                resultText.text = RecognitionPolicy.describeError(e)
                if (e is SecurityException) showPage(searchVisible)
            }
        }) { hits ->
            if (generation != searchGeneration) return@read
            selection.replaceResults(hits.map { key(it.record) })
            restore.forEach { selection.setSelected(it, true) }
            refreshingKeys = emptyList()
            adapter.hits = hits; adapter.notifyDataSetChanged()
            updateSelection()
            resultText.text = "找到 " + hits.size + " 张" +
                if (hits.isEmpty()) "。仅搜索已识别、未改变且仍可访问的图片。" else " · 点击预览，或批量选择"
        }
    }

    private fun key(record: RecognitionRecord) = MediaItem(record.uri, record.size, record.lastModified).versionKey

    private fun updateSelection() {
        bulkButton.text = if (bulkMode) "退出选择" else "批量选择"
        bulkButton.isEnabled = adapter.hits.isNotEmpty() && !sharing
        selectionText.text = if (sharing) "正在检查图片…" else "已选 ${selection.size} 张"
        selectionText.visibility = if (bulkMode) View.VISIBLE else View.GONE
        bulkActions.visibility = if (bulkMode) View.VISIBLE else View.GONE
        selectAllButton.isEnabled = adapter.hits.isNotEmpty() && !sharing
        clearButton.isEnabled = selection.size > 0 && !sharing
        shareButton.isEnabled = selection.size > 0 && !sharing
        grid.isEnabled = !sharing
    }

    private inner class HitAdapter : BaseAdapter() {
        var hits = emptyList<SearchHit>()
        override fun getCount() = hits.size
        override fun getItem(position: Int) = hits[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val cell = (convertView as? FrameLayout) ?: FrameLayout(this@MainActivity).apply {
                layoutParams = AbsListView.LayoutParams(-1, dp(128))
                addView(ImageView(this@MainActivity).apply {
                    scaleType = ImageView.ScaleType.FIT_CENTER
                }, FrameLayout.LayoutParams(-1, -1))
                addView(CheckBox(this@MainActivity).apply {
                    isClickable = false; isFocusable = false
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                    buttonTintList = ColorStateList.valueOf(teal)
                }, FrameLayout.LayoutParams(dp(40), dp(40), Gravity.TOP or Gravity.END))
            }
            val image = cell.getChildAt(0) as ImageView
            val check = cell.getChildAt(1) as CheckBox
            val record = hits[position].record
            val key = MediaItem(record.uri, record.size, record.lastModified).versionKey
            val checked = selection.contains(key)
            check.visibility = if (bulkMode) View.VISIBLE else View.GONE
            check.isChecked = checked
            cell.setBackgroundColor(if (bulkMode && checked) Color.rgb(208, 235, 229) else Color.WHITE)
            cell.contentDescription = (if (bulkMode) if (checked) "已选择图片：" else "未选择图片：" else "匹配图片：") + record.text.take(80)
            image.tag = key
            image.setImageDrawable(null)
            thumbs.execute {
                val bitmap = try { ImageLoader.thumbnail(key) { reader.openInputStream(record.uri) } }
                             catch (_: Exception) { null }
                main.post { if (!disposed && image.tag == key) image.setImageBitmap(bitmap) }
            }
            return cell
        }
    }

    private fun preview(hit: SearchHit) {
        val image = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = "图片预览"
        }
        val content = column().apply {
            setPadding(dp(16), 0, dp(16), 0)
            addView(image, LinearLayout.LayoutParams(-1, dp(300)))
            full(text(hit.record.text.take(2000), 14f).apply { setTextIsSelectable(true) })
        }
        val scroll = ScrollView(this).apply { addView(content) }
        val dialog = AlertDialog.Builder(this).setTitle("图片预览").setView(scroll)
            .setPositiveButton("分享图片") { _, _ -> share(listOf(hit.record)) }
            .setNegativeButton("关闭", null).show()
        read({
            val current = reader.statUri(hit.record.uri)
            if (current == null || !hit.record.matchesVersion(current)) throw java.io.IOException("图片已改变或不可访问，请重新搜索")
            ImageLoader.decodeForOcr { reader.openInputStream(hit.record.uri) }
        }) { bitmap ->
            if (dialog.isShowing) image.setImageBitmap(bitmap) else bitmap?.recycle()
        }
    }

    private fun share(records: List<RecognitionRecord>) {
        if (sharing || records.isEmpty()) return
        val generation = ++shareGeneration
        sharing = true; updateSelection()
        read({
            records.distinctBy { it.uri }.map { record ->
                if (generation != shareGeneration) throw CancellationException("已取消分享")
                val current = reader.statUri(record.uri)
                if (current == null || !record.matchesVersion(current))
                    throw java.io.IOException("所选图片已改变或不可访问，请重新搜索")
                reader.openInputStream(record.uri)?.use { it.read() }
                    ?: throw java.io.IOException("所选图片不可读取，请重新搜索")
                val after = reader.statUri(record.uri)
                if (after == null || !record.matchesVersion(after))
                    throw java.io.IOException("所选图片在检查期间发生变化，请重新搜索")
                Uri.parse(record.uri)
            }
        }, failed = { e ->
            if (generation == shareGeneration) {
                sharing = false; updateSelection(); toast(RecognitionPolicy.describeError(e))
                if (e is SecurityException) showPage(searchVisible)
            }
        }) { uris ->
            if (generation != shareGeneration) return@read
            sharing = false; updateSelection()
            try { startActivity(Intent.createChooser(ShareIntents.images(uris), "分享图片")) }
            catch (e: Exception) {
                toast(if (e is TransactionTooLargeException || e.cause is TransactionTooLargeException)
                    "选择的图片过多，请减少数量后再分享" else "没有可用的分享应用，或图片已不可访问")
            }
        }
    }
    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    override fun onResume() {
        super.onResume()
        showPage(searchVisible)
        // Permissions and media may have changed while this activity was away.
        val keys = restoredKeys ?: if (refreshingKeys.isNotEmpty()) refreshingKeys else selection.selectedKeys()
        restoredKeys = null
        if (hasPhotos() && hasSearched) search(keys, restoring = true)
        else {
            searchGeneration++; adapter.hits = emptyList(); adapter.notifyDataSetChanged()
            selection.replaceResults(emptyList()); updateSelection()
        }
        if (hasPhotos()) refreshAlbums()
        previousProgress = null
        main.removeCallbacks(poll); main.post(poll)
    }
    override fun onSaveInstanceState(state: Bundle) {
        state.putString("query", searchInput.text.toString())
        state.putBoolean("regex", regex.isChecked)
        state.putBoolean("searchVisible", searchVisible)
        state.putBoolean("hasSearched", hasSearched)
        state.putString("resultQuery", resultQuery)
        state.putBoolean("resultRegex", resultRegex)
        state.putBoolean("bulkMode", bulkMode)
        super.onSaveInstanceState(state)
    }
    override fun onRetainNonConfigurationInstance(): Any =
        RetainedSelection(if (refreshingKeys.isNotEmpty()) refreshingKeys else selection.selectedKeys())
    override fun onPause() {
        shareGeneration++; sharing = false; updateSelection()
        main.removeCallbacks(poll); super.onPause()
    }
    override fun onDestroy() {
        disposed = true; main.removeCallbacksAndMessages(null)
        thumbs.shutdownNow()
        io.execute { db.close() }; io.shutdown()
        super.onDestroy()
    }
}
