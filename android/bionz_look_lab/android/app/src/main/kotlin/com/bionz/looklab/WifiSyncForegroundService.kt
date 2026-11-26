package com.bionz.looklab

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.io.File
import java.io.IOException
import java.net.Inet4Address
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

class WifiSyncForegroundService : Service() {

    private val running = AtomicBoolean(false)
    private val syncSignal = Object()
    private var workerThread: Thread? = null
    private val postProcessExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "WiFiSyncPostProc")
    }

    private lateinit var store: SyncStore
    private lateinit var connectivityManager: ConnectivityManager
    private lateinit var wifiManager: WifiManager
    private lateinit var powerManager: PowerManager
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null

    @Volatile
    private var boundCameraNetwork: Network? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    private val postTransferHook: PostTransferHook by lazy {
        object : PostTransferHook {
            override val name: String
                get() = currentHookLabel(this@WifiSyncForegroundService)

            override fun onFileVerified(record: SyncedFileRecord, verifiedFile: File?) {
                val preset = LookPresetStore(this@WifiSyncForegroundService).getAutoApplyPreset() ?: return
                try {
                    val res = LookLabImageIO.exportGradedToMediaStore(
                        this@WifiSyncForegroundService,
                        record.dcfName,
                        preset,
                        sourceFile = verifiedFile
                    )
                    log("Auto-graded ${record.dcfName} [${preset.name}] (${res.elapsedMs} ms) -> ${res.localPath}")
                } catch (t: Throwable) {
                    log("Auto-grade skipped (${record.dcfName}): ${t.message}")
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        store = SyncStore(this)
        connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        ensureNotificationChannel()
        updateState {
            syncedCount = store.totalCount()
            recentFiles = store.recentFiles()
            hookName = postTransferHook.name
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSyncLoop()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                } else {
                    @Suppress("DEPRECATION")
                    stopForeground(true)
                }
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SYNC_NOW -> {
                log("Manual sync trigger")
                wakeWorker()
                if (!running.get()) startSyncLoop()
                return START_STICKY
            }
            else -> {
                startInForeground("Searching for A6400 5 GHz AP...")
                startSyncLoop()
                return START_STICKY
            }
        }
    }

    override fun onDestroy() {
        stopSyncLoop()
        postProcessExecutor.shutdown()
        try {
            postProcessExecutor.awaitTermination(5, TimeUnit.SECONDS)
        } catch (_: InterruptedException) {
        }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startInForeground(contentText: String) {
        val notif = buildNotification(contentText)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NOTIFICATION_ID, notif)
        }
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                val ch = NotificationChannel(
                    CHANNEL_ID,
                    "Camera 5 GHz Background Sync",
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = "Keeps Sony A6400 5 GHz transfer alive while screen is locked."
                    setShowBadge(false)
                }
                nm.createNotificationChannel(ch)
            }
        }
    }

    private fun buildNotification(text: String): Notification {
        val launchIntent = Intent(this, MainActivity::class.java)
        val pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val contentPending = PendingIntent.getActivity(this, 0, launchIntent, pendingFlags)

        val stopIntent = Intent(this, WifiSyncForegroundService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPending = PendingIntent.getService(this, 1, stopIntent, pendingFlags)

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("BIONZ Look Lab • WiFiSync")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(contentPending)
            .addAction(android.R.drawable.ic_media_pause, "Stop", stopPending)
            .build()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun startSyncLoop() {
        if (!running.compareAndSet(false, true)) {
            wakeWorker()
            return
        }
        val cfg = SyncConfig.load(this)
        registerWifiSuggestion(cfg)
        registerNetworkCallback(cfg)
        acquireWifiLock()

        updateState {
            serviceRunning = true
            connectionState = "Connecting"
            ssid = cfg.ssid
            host = cfg.host
            port = cfg.port
            lastError = ""
            hookName = postTransferHook.name
        }
        log("Foreground sync started (${cfg.ssid} -> ${cfg.host}:${cfg.port})")

        workerThread = Thread({ runSyncLoop() }, "WiFiSyncWorker").apply { start() }
    }

    private fun stopSyncLoop() {
        if (!running.compareAndSet(true, false)) return
        wakeWorker()
        unregisterNetworkCallback()
        try {
            workerThread?.join(1500)
        } catch (_: InterruptedException) {
        }
        workerThread = null
        releaseTransferWakeLock()
        releaseWifiLock()
        boundCameraNetwork = null
        updateState {
            serviceRunning = false
            connectionState = "Stopped"
            activeFile = ""
            progress = 0.0
            speedMbps = 0.0
        }
        log("Foreground sync stopped")
    }

    private fun wakeWorker() {
        synchronized(syncSignal) {
            syncSignal.notifyAll()
        }
    }

    private fun sleepWithSignal(delayMs: Long) {
        synchronized(syncSignal) {
            try {
                syncSignal.wait(delayMs)
            } catch (_: InterruptedException) {
            }
        }
    }

    private fun registerWifiSuggestion(cfg: SyncConfig) {
        if (!cfg.autoSuggestWifi || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        if (cfg.psk.length !in 8..63) {
            log("Skipped WifiNetworkSuggestion: passphrase must be 8..63 chars")
            return
        }
        try {
            val suggestion = WifiNetworkSuggestion.Builder()
                .setSsid(cfg.ssid)
                .setWpa2Passphrase(cfg.psk)
                .setIsAppInteractionRequired(false)
                .build()
            val status = wifiManager.addNetworkSuggestions(listOf(suggestion))
            log("WifiNetworkSuggestion(${cfg.ssid}) rc=$status")
        } catch (t: Throwable) {
            log("WifiNetworkSuggestion error: ${t.message}")
        }
    }

    private fun hostSubnetPrefix(host: String): String {
        val lastDot = host.lastIndexOf('.')
        return if (lastDot > 0) host.substring(0, lastDot + 1) else host
    }

    private fun hasMatchingSubnet(lp: LinkProperties?, prefix: String): Boolean {
        if (lp == null || prefix.isEmpty()) return false
        for (la in lp.linkAddresses) {
            val addr = la.address
            if (addr is Inet4Address && (addr.hostAddress ?: "").startsWith(prefix)) {
                return true
            }
        }
        return false
    }

    private fun isCameraWifiNetwork(
        network: Network,
        cfg: SyncConfig,
        lp: LinkProperties? = connectivityManager.getLinkProperties(network),
        caps: NetworkCapabilities? = connectivityManager.getNetworkCapabilities(network)
    ): Boolean {
        if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) return false
        val sameSubnet = hasMatchingSubnet(lp, hostSubnetPrefix(cfg.host))
        val validated = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        return if (cfg.host == SyncConfig.DEFAULT_HOST) {
            sameSubnet || !validated
        } else {
            sameSubnet || true
        }
    }

    private fun tryBindCameraNetwork(
        network: Network,
        cfg: SyncConfig,
        lp: LinkProperties? = connectivityManager.getLinkProperties(network),
        caps: NetworkCapabilities? = connectivityManager.getNetworkCapabilities(network)
    ) {
        if (!isCameraWifiNetwork(network, cfg, lp, caps)) return
        if (boundCameraNetwork == network) return
        val sameSubnet = hasMatchingSubnet(lp, hostSubnetPrefix(cfg.host))
        val validated = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true
        boundCameraNetwork = network
        log("Bound Wi-Fi network $network (subnet=$sameSubnet validated=$validated)")
        wakeWorker()
    }

    private fun registerNetworkCallback(cfg: SyncConfig) {
        unregisterNetworkCallback()
        @Suppress("DEPRECATION")
        for (net in connectivityManager.allNetworks) {
            tryBindCameraNetwork(net, cfg)
            if (boundCameraNetwork != null) break
        }
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                tryBindCameraNetwork(network, cfg)
            }

            override fun onLinkPropertiesChanged(network: Network, linkProperties: LinkProperties) {
                tryBindCameraNetwork(network, cfg, lp = linkProperties)
            }

            override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) {
                tryBindCameraNetwork(network, cfg, caps = networkCapabilities)
            }

            override fun onLost(network: Network) {
                if (boundCameraNetwork == network) {
                    boundCameraNetwork = null
                    log("Wi-Fi network lost")
                }
            }
        }
        networkCallback = cb
        try {
            connectivityManager.registerNetworkCallback(request, cb)
        } catch (t: Throwable) {
            log("NetworkCallback error: ${t.message}")
        }
    }

    private fun unregisterNetworkCallback() {
        networkCallback?.let { cb ->
            try {
                connectivityManager.unregisterNetworkCallback(cb)
            } catch (_: Throwable) {
            }
        }
        networkCallback = null
    }

    private fun acquireWifiLock() {
        if (wifiLock?.isHeld == true) return
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            WifiManager.WIFI_MODE_FULL_LOW_LATENCY
        } else {
            @Suppress("DEPRECATION")
            WifiManager.WIFI_MODE_FULL_HIGH_PERF
        }
        wifiLock = wifiManager.createWifiLock(mode, "bionz:wifisync").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseWifiLock() {
        try {
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Throwable) {
        }
    }

    private fun acquireTransferWakeLock() {
        if (wakeLock?.isHeld == true) return
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "bionz:transfer").apply {
            setReferenceCounted(false)
            acquire(10 * 60 * 1000L)
        }
    }

    private fun releaseTransferWakeLock() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Throwable) {
        }
    }

    private fun drainVerifiedStaging() {
        val root = File(filesDir, "verified")
        val camDirs = root.listFiles() ?: return
        for (camDir in camDirs) {
            if (!camDir.isDirectory) continue
            val camId = camDir.name
            val staged = camDir.listFiles() ?: continue
            for (vf in staged) {
                if (!vf.isFile) continue
                postProcessExecutor.execute {
                    try {
                        if (!store.isSynced(camId, vf.name)) {
                            val t0 = System.nanoTime()
                            val savedPath = CameraHttpClient.publishToMediaStore(this, vf, vf.name)
                            val pubMs = (System.nanoTime() - t0) / 1_000_000L
                            val crcHex = CameraHttpClient.computeFileCrc32Hex(vf)
                            val record = SyncedFileRecord(
                                cameraId = camId,
                                dcfName = vf.name,
                                seqId = 0,
                                sizeBytes = vf.length(),
                                crc32Hex = crcHex,
                                localPath = savedPath,
                                syncedAtMs = System.currentTimeMillis()
                            )
                            store.recordSynced(record)
                            postTransferHook.onFileVerified(record, vf)
                            updateState {
                                syncedCount = store.totalCount()
                                recentFiles = store.recentFiles()
                                hookName = postTransferHook.name
                            }
                            log("Recovered ${vf.name} (pub=${pubMs}ms) -> $savedPath")
                        }
                    } catch (t: Throwable) {
                        log("Recovery failed (${vf.name}): ${t.message}")
                    } finally {
                        vf.delete()
                    }
                }
            }
        }
    }

    private fun runSyncLoop() {
        drainVerifiedStaging()
        var backoffMs = 1000L
        while (running.get()) {
            val cfg = SyncConfig.load(this)
            val client = CameraHttpClient(cfg.host, cfg.port, requireBoundNetwork = true) { boundCameraNetwork }
            try {
                val status = client.fetchStatus()
                backoffMs = 1000L
                val chLabel = if (status.apChanspec > 0) {
                    "Ch ${status.apChanspec} (${status.apWidth} MHz)"
                } else {
                    "5 GHz"
                }
                updateState {
                    connectionState = "Connected"
                    ssid = cfg.ssid
                    cameraId = status.cameraId
                    powerState = status.powerStateLabel()
                    chanspec = chLabel
                    queueLen = status.queueLen
                    lastError = ""
                }
                updateNotification("Connected (${cfg.ssid}) • ${stateSnapshotSynced()} synced")

                var watermark = store.getWatermark(status.cameraId)
                if (watermark > status.queueHead) {
                    log("Camera queue reset (local watermark=$watermark > head=${status.queueHead}); syncing from ${status.ack}")
                    store.resetWatermark(status.cameraId, status.ack)
                    watermark = status.ack
                }
                val entries = client.fetchManifestAll(watermark)
                if (entries.isNotEmpty()) {
                    acquireTransferWakeLock()
                    try {
                        for (entry in entries) {
                            if (!running.get()) break
                            if (store.isSynced(status.cameraId, entry.name)) {
                                val nextAck = client.postAck(entry.seqId, entry.sizeBytes, entry.crc32Hex)
                                store.updateWatermark(status.cameraId, nextAck)
                                continue
                            }
                            transferOneFile(client, status.cameraId, entry, cfg)
                        }
                    } finally {
                        releaseTransferWakeLock()
                    }
                    updateState {
                        activeFile = ""
                        progress = 0.0
                        speedMbps = 0.0
                    }
                }

                updateNotification("Idle (${cfg.ssid}) • ${stateSnapshotSynced()} synced")
                client.waitEvent(status.queueHead)
            } catch (t: Throwable) {
                releaseTransferWakeLock()
                if (!running.get()) break
                val msg = t.message ?: t.javaClass.simpleName
                updateState {
                    connectionState = "Waiting ($backoffMs ms)"
                    lastError = msg
                    activeFile = ""
                    progress = 0.0
                    speedMbps = 0.0
                }
                updateNotification("Waiting for ${cfg.ssid}...")
                log("Retry in ${backoffMs}ms: $msg")
                sleepWithSignal(backoffMs)
                backoffMs = minOf(backoffMs * 2L, 10_000L)
            }
        }
    }

    private fun transferOneFile(
        client: CameraHttpClient,
        cameraId: String,
        entry: ManifestEntry,
        cfg: SyncConfig
    ) {
        if (cfg.arwInstantPreview && entry.name.endsWith(".ARW", ignoreCase = true)) {
            val prevT0 = System.nanoTime()
            try {
                val prevBytes = client.downloadPreview(entry.seqId)
                if (prevBytes != null) {
                    LookLabImageIO.putCachedPreviewBytes(entry.name, prevBytes)
                    val prevMs = (System.nanoTime() - prevT0) / 1_000_000L
                    val stem = entry.name.substringBeforeLast('.', entry.name)
                    val previewName = "${stem}_preview.JPG"
                    val interimRecord = SyncedFileRecord(
                        cameraId = cameraId,
                        dcfName = entry.name,
                        seqId = entry.seqId,
                        sizeBytes = prevBytes.size.toLong(),
                        crc32Hex = "preview",
                        localPath = "${android.os.Environment.DIRECTORY_DCIM}/BionzLookLab/$previewName",
                        syncedAtMs = System.currentTimeMillis()
                    )
                    updateState {
                        recentFiles = listOf(interimRecord) + recentFiles.filter { it.dcfName != entry.name }
                    }
                    postProcessExecutor.execute {
                        try {
                            val savedPrev = CameraHttpClient.publishBytesToMediaStore(
                                this,
                                prevBytes,
                                previewName
                            )
                            log("Saved instant preview $previewName (${prevBytes.size} B, ${prevMs} ms) -> $savedPrev")
                        } catch (t: Throwable) {
                            log("Preview publish failed ($previewName): ${t.message}")
                        }
                    }
                }
            } catch (t: Throwable) {
                log("Instant preview skipped (${entry.name}): ${t.message}")
            }
        }

        val partDir = File(filesDir, "parts/$cameraId")
        val partFile = File(partDir, "${entry.name}.part")
        val autoPreset = LookPresetStore(this).getAutoApplyPreset()
        val startedNs = System.nanoTime()
        var lastProgressNs = 0L
        log("Downloading ${entry.name} (${entry.sizeBytes} B, seq=${entry.seqId})")

        val progressCb: (Long, Long) -> Unit = { rx, total ->
            val nowNs = System.nanoTime()
            if (rx >= total || nowNs - lastProgressNs >= 100_000_000L) {
                lastProgressNs = nowNs
                val elapsedSec = maxOf((nowNs - startedNs) / 1e9, 0.05)
                val mbs = rx / (1024.0 * 1024.0) / elapsedSec
                val pct = if (total > 0L) (rx.toDouble() / total.toDouble()).coerceIn(0.0, 1.0) else 0.0
                updateState {
                    activeFile = entry.name
                    progress = pct
                    speedMbps = mbs
                }
            }
        }

        if (autoPreset == null && !partFile.isFile) {
            val (dl, savedPath) = client.downloadToMediaStoreDirect(this, entry, progressCb)
            val dlMs = (System.nanoTime() - startedNs) / 1_000_000L
            val elapsedSec = maxOf(dlMs / 1000.0, 0.01)
            val finalMbs = dl.sizeBytes / (1024.0 * 1024.0) / elapsedSec

            val nextAck = client.postAck(entry.seqId, dl.sizeBytes, dl.crc32Hex)
            store.updateWatermark(cameraId, nextAck)

            val record = SyncedFileRecord(
                cameraId = cameraId,
                dcfName = entry.name,
                seqId = entry.seqId,
                sizeBytes = dl.sizeBytes,
                crc32Hex = dl.crc32Hex,
                localPath = savedPath,
                syncedAtMs = System.currentTimeMillis()
            )
            postProcessExecutor.execute {
                LookLabImageIO.invalidateCache(entry.name)
                store.recordSynced(record)
                updateState {
                    syncedCount = store.totalCount()
                    recentFiles = store.recentFiles()
                    speedMbps = finalMbs
                    hookName = postTransferHook.name
                }
            }
            log("Synced ${entry.name} [${String.format(Locale.US, "%.2f", finalMbs)} MB/s] direct dl=${dlMs}ms CRC=${dl.crc32Hex}")
            return
        }

        val dl = client.downloadWithResume(entry, partFile, progressCb)
        val dlMs = (System.nanoTime() - startedNs) / 1_000_000L
        val elapsedSec = maxOf(dlMs / 1000.0, 0.01)
        val finalMbs = (dl.sizeBytes - dl.resumedFromBytes) / (1024.0 * 1024.0) / elapsedSec

        val verifiedDir = File(filesDir, "verified/$cameraId")
        verifiedDir.mkdirs()
        val verifiedFile = File(verifiedDir, entry.name)
        verifiedFile.delete()
        if (!partFile.renameTo(verifiedFile)) {
            throw IOException("Failed to stage verified file ${entry.name}")
        }

        val nextAck = client.postAck(entry.seqId, dl.sizeBytes, dl.crc32Hex)
        store.updateWatermark(cameraId, nextAck)

        postProcessExecutor.execute {
            try {
                val pubT0 = System.nanoTime()
                val savedPath = CameraHttpClient.publishToMediaStore(this, verifiedFile, entry.name)
                val pubMs = (System.nanoTime() - pubT0) / 1_000_000L
                val record = SyncedFileRecord(
                    cameraId = cameraId,
                    dcfName = entry.name,
                    seqId = entry.seqId,
                    sizeBytes = dl.sizeBytes,
                    crc32Hex = dl.crc32Hex,
                    localPath = savedPath,
                    syncedAtMs = System.currentTimeMillis()
                )
                LookLabImageIO.invalidateCache(entry.name)
                store.recordSynced(record)
                val gradeT0 = System.nanoTime()
                postTransferHook.onFileVerified(record, verifiedFile)
                val gradeMs = (System.nanoTime() - gradeT0) / 1_000_000L
                updateState {
                    syncedCount = store.totalCount()
                    recentFiles = store.recentFiles()
                    speedMbps = finalMbs
                    hookName = postTransferHook.name
                }
                val resumedTag = if (dl.resumedFromBytes > 0L) " (resumed from ${dl.resumedFromBytes})" else ""
                log("Synced ${entry.name} [${String.format(Locale.US, "%.2f", finalMbs)} MB/s] dl=${dlMs}ms pub=${pubMs}ms grade=${gradeMs}ms CRC=${dl.crc32Hex}$resumedTag")
            } catch (t: Throwable) {
                log("Post-process failed (${entry.name}): ${t.message}")
            } finally {
                verifiedFile.delete()
            }
        }
    }

    private fun log(message: String) {
        val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        val line = "[$ts] $message"
        updateState {
            logs.add(0, line)
            if (logs.size > 60) logs.removeAt(logs.lastIndex)
        }
    }

    companion object {
        const val ACTION_START = "com.bionz.looklab.START"
        const val ACTION_STOP = "com.bionz.looklab.STOP"
        const val ACTION_SYNC_NOW = "com.bionz.looklab.SYNC_NOW"
        private const val CHANNEL_ID = "bionz_wifisync_channel"
        private const val NOTIFICATION_ID = 4339

        private val stateLock = ReentrantLock()
        private val listeners = CopyOnWriteArraySet<(Map<String, Any>) -> Unit>()

        private class MutableLiveState {
            var serviceRunning: Boolean = false
            var connectionState: String = "Stopped"
            var ssid: String = SyncConfig.DEFAULT_SSID
            var host: String = SyncConfig.DEFAULT_HOST
            var port: Int = SyncConfig.DEFAULT_PORT
            var cameraId: String = ""
            var powerState: String = "OFF"
            var chanspec: String = "-"
            var queueLen: Int = 0
            var activeFile: String = ""
            var progress: Double = 0.0
            var speedMbps: Double = 0.0
            var syncedCount: Int = 0
            var lastError: String = ""
            var hookName: String = "NoOpPostTransferHook (Transfer-only v1)"
            var recentFiles: List<SyncedFileRecord> = emptyList()
            val logs: ArrayList<String> = ArrayList()

            fun toMap(): Map<String, Any> = mapOf(
                "serviceRunning" to serviceRunning,
                "connectionState" to connectionState,
                "ssid" to ssid,
                "host" to host,
                "port" to port,
                "cameraId" to cameraId,
                "powerState" to powerState,
                "chanspec" to chanspec,
                "queueLen" to queueLen,
                "activeFile" to activeFile,
                "progress" to progress,
                "speedMbps" to speedMbps,
                "syncedCount" to syncedCount,
                "lastError" to lastError,
                "hookName" to hookName,
                "recentFiles" to recentFiles.map { it.toMap() },
                "logs" to logs.toList()
            )
        }

        private val liveState = MutableLiveState()

        private fun stateSnapshotSynced(): Int = stateLock.withLock { liveState.syncedCount }

        private fun updateState(block: MutableLiveState.() -> Unit) {
            val snap = stateLock.withLock {
                liveState.block()
                liveState.toMap()
            }
            for (listener in listeners) {
                listener(snap)
            }
        }

        fun getSnapshot(context: Context): Map<String, Any> {
            return stateLock.withLock {
                if (liveState.recentFiles.isEmpty()) {
                    val store = SyncStore(context)
                    liveState.syncedCount = store.totalCount()
                    liveState.recentFiles = store.recentFiles()
                }
                val cfg = SyncConfig.load(context)
                if (!liveState.serviceRunning) {
                    liveState.ssid = cfg.ssid
                    liveState.host = cfg.host
                    liveState.port = cfg.port
                }
                liveState.hookName = currentHookLabel(context)
                liveState.toMap()
            }
        }

        fun currentHookLabel(context: Context): String {
            val active = LookPresetStore(context).getAutoApplyPreset()
            return if (active != null) {
                "SpektraFilm Auto: ${active.name}"
            } else {
                "SpektraFilm (Auto-Apply OFF)"
            }
        }

        fun refreshHookLabel(context: Context) {
            val label = currentHookLabel(context)
            updateState {
                hookName = label
            }
        }

        fun clearLogs() {
            updateState { logs.clear() }
        }

        fun addListener(listener: (Map<String, Any>) -> Unit) {
            listeners.add(listener)
        }

        fun removeListener(listener: (Map<String, Any>) -> Unit) {
            listeners.remove(listener)
        }
    }
}
