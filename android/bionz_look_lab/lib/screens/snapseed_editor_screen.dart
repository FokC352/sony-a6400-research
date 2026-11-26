import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../models/look_models.dart';
import '../models/sync_models.dart';
import '../services/sync_bridge.dart';

class _ShaderRenderState {
  const _ShaderRenderState({
    required this.preset,
    required this.splitMode,
    required this.splitPos,
    required this.isLog2,
  });

  final LookPreset preset;
  final int splitMode; // 0 = graded, 1 = split, 2 = original
  final double splitPos;
  final bool isLog2;
}

/// Zero-lag GPU Impeller GLSL + 14-bit RAW Develop & SpektraFilm studio editor.
class SnapseedEditorScreen extends StatefulWidget {
  const SnapseedEditorScreen({
    super.key,
    required this.bridge,
    required this.file,
    required this.initialCatalog,
    this.initialPreset,
    this.onCatalogChanged,
  });

  final SyncBridge bridge;
  final SyncedFile file;
  final LookCatalog initialCatalog;
  final LookPreset? initialPreset;
  final ValueChanged<LookCatalog>? onCatalogChanged;

  @override
  State<SnapseedEditorScreen> createState() => _SnapseedEditorScreenState();
}

class _SnapseedEditorScreenState extends State<SnapseedEditorScreen> {
  static const _accent = Color(0xFFFF6D00);
  static const _bgCanvas = Color(0xFF0B0D11);
  static const _bgPanel = Color(0xFF141820);
  static const _bgChip = Color(0xFF1E2430);

  late LookCatalog _catalog;
  late LookPreset _preset;
  late SyncedFile _liveFile;

  ToolCategory _category = ToolCategory.light;
  AdjustParam _activeParam = AdjustParam.exposure;

  final List<LookPreset> _history = [];
  int _historyIdx = -1;

  // GPU Shader session state
  ui.FragmentProgram? _shaderProgram;
  RawEditorSession? _rawSession;
  ui.Image? _gpuImage;
  late final ValueNotifier<_ShaderRenderState> _shaderState;

  // Headless test / fallback state
  Uint8List? _originalBytes;
  Uint8List? _previewBytes;
  HistogramData? _histogram;

  bool _loading = true;
  bool _exporting = false;
  bool _comparingOriginal = false;
  bool _splitCompare = false;
  double _splitPos = 0.5;
  bool _showHistogram = true;

  Timer? _histThrottle;
  Timer? _fallbackDebounce;
  StreamSubscription<SyncSnapshot>? _eventSub;
  int _loadTicket = 0;

  @override
  void initState() {
    super.initState();
    _liveFile = widget.file;
    _catalog = widget.initialCatalog;
    final auto = _catalog.findPreset(_catalog.autoApplyPresetId);
    _preset = (widget.initialPreset ??
            auto ??
            (_catalog.presets.isNotEmpty
                ? _catalog.presets.first
                : const LookPreset(
                    id: 'builtin_portra_400',
                    name: 'Kodak Portra 400',
                  )))
        .copyWith();
    _shaderState = ValueNotifier<_ShaderRenderState>(
      _ShaderRenderState(
        preset: _preset,
        splitMode: 0,
        splitPos: _splitPos,
        isLog2: false,
      ),
    );
    _pushHistory(_preset);

    _eventSub = widget.bridge.events.listen((snap) {
      for (final f in snap.recentFiles) {
        if (f.dcfName == _liveFile.dcfName) {
          final upgraded = _liveFile.isPreview && !f.isPreview;
          if (f.crc32Hex != _liveFile.crc32Hex || f.sizeBytes != _liveFile.sizeBytes) {
            if (!mounted) return;
            setState(() => _liveFile = f);
            if (upgraded) {
              _loadInitialFrames();
            }
          }
          break;
        }
      }
    });

    _initAndLoad();
  }

  @override
  void dispose() {
    _histThrottle?.cancel();
    _fallbackDebounce?.cancel();
    _eventSub?.cancel();
    _shaderState.dispose();
    _gpuImage?.dispose();
    super.dispose();
  }

  Future<void> _initAndLoad() async {
    try {
      _shaderProgram ??= await ui.FragmentProgram.fromAsset(
        'shaders/spektrafilm_raw.frag',
      );
    } catch (_) {
      // Headless widget tests without compiled shader asset use fallback path.
      _shaderProgram = null;
    }
    await _loadInitialFrames();
  }

  void _syncShaderNotifier() {
    final mode = _comparingOriginal ? 2 : (_splitCompare ? 1 : 0);
    _shaderState.value = _ShaderRenderState(
      preset: _preset,
      splitMode: mode,
      splitPos: _splitPos,
      isLog2: _rawSession?.isLog2 ?? false,
    );
  }

  void _pushHistory(LookPreset p) {
    if (_historyIdx >= 0 && _historyIdx < _history.length - 1) {
      _history.removeRange(_historyIdx + 1, _history.length);
    }
    if (_history.isEmpty || _history.last.toMap().toString() != p.toMap().toString()) {
      _history.add(p.copyWith());
      _historyIdx = _history.length - 1;
    }
  }

  bool get _canUndo => _historyIdx > 0;
  bool get _canRedo => _historyIdx >= 0 && _historyIdx < _history.length - 1;

  void _undo() {
    if (!_canUndo) return;
    setState(() {
      _historyIdx--;
      _preset = _history[_historyIdx].copyWith();
    });
    _onLookMutated(immediateHist: true);
  }

  void _redo() {
    if (!_canRedo) return;
    setState(() {
      _historyIdx++;
      _preset = _history[_historyIdx].copyWith();
    });
    _onLookMutated(immediateHist: true);
  }

  void _resetAll() {
    final next = _preset.resetAdjustments(
      keepStockId: 'neutral',
      keepPaperId: 'neutral_scan',
    );
    setState(() => _preset = next);
    _pushHistory(next);
    _onLookMutated(immediateHist: true);
  }

  Future<ui.Image> _decodeRgbaImage(Uint8List bytes, int w, int h) {
    final c = Completer<ui.Image>();
    ui.decodeImageFromPixels(
      bytes,
      w,
      h,
      ui.PixelFormat.rgba8888,
      c.complete,
    );
    return c.future;
  }

  Future<void> _loadInitialFrames() async {
    final ticket = ++_loadTicket;
    if (_shaderProgram != null) {
      final session = await widget.bridge.loadRawEditorSession(_liveFile.dcfName);
      if (!mounted || ticket != _loadTicket) return;
      if (session != null) {
        final decodedImg = await _decodeRgbaImage(
          session.rgbaBytes,
          session.width,
          session.height,
        );
        if (!mounted || ticket != _loadTicket) {
          decodedImg.dispose();
          return;
        }
        final oldImg = _gpuImage;
        setState(() {
          _rawSession = session;
          _gpuImage = decodedImg;
          _loading = false;
        });
        oldImg?.dispose();
        _syncShaderNotifier();
        _updateSampleHistogramNow();
        return;
      }
    }

    // Fallback (headless tests or unexpected platform failure)
    const neutral = LookPreset(
      id: '__neutral__',
      name: 'Original',
      stockId: 'neutral',
      paperId: 'neutral_scan',
      dirCouplers: 0,
      halation: 0,
      grain: 0,
    );
    final results = await Future.wait([
      widget.bridge.renderPhotoPreview(_liveFile.dcfName, neutral),
      _fetchFallbackRender(_liveFile.dcfName, _preset),
    ]);
    if (!mounted || ticket != _loadTicket) return;
    final orig = results[0] as Uint8List?;
    final graded = results[1] as PreviewRenderResult?;
    setState(() {
      if (orig != null) _originalBytes = orig;
      if (graded != null) {
        _previewBytes = graded.jpegBytes;
        _histogram = graded.histogram;
      } else if (orig != null) {
        _previewBytes ??= orig;
      }
      _loading = false;
    });
  }

  Future<PreviewRenderResult?> _fetchFallbackRender(String dcfName, LookPreset p) async {
    final withHist = await widget.bridge.renderPhotoPreviewWithHistogram(dcfName, p);
    if (withHist != null) return withHist;
    final bytes = await widget.bridge.renderPhotoPreview(dcfName, p);
    if (bytes == null) return null;
    return PreviewRenderResult(jpegBytes: bytes);
  }

  void _onLookMutated({bool immediateHist = false}) {
    if (_gpuImage != null && _shaderProgram != null) {
      _syncShaderNotifier();
      if (immediateHist) {
        _histThrottle?.cancel();
        _updateSampleHistogramNow();
      } else {
        _scheduleSampleHistogram();
      }
      return;
    }

    _fallbackDebounce?.cancel();
    _fallbackDebounce = Timer(const Duration(milliseconds: 45), () async {
      final res = await _fetchFallbackRender(_liveFile.dcfName, _preset);
      if (!mounted || res == null) return;
      setState(() {
        _previewBytes = res.jpegBytes;
        if (res.histogram != null) _histogram = res.histogram;
      });
    });
  }

  void _scheduleSampleHistogram() {
    if (!_showHistogram || _rawSession == null) return;
    if (_histThrottle?.isActive ?? false) return;
    _histThrottle = Timer(const Duration(milliseconds: 80), () {
      if (mounted) _updateSampleHistogramNow();
    });
  }

  void _updateSampleHistogramNow() {
    final s = _rawSession;
    if (s == null || s.histRgb.isEmpty || !_showHistogram) return;
    final hist = _computeFastSampleHistogram(s, _preset);
    if (!mounted) return;
    setState(() => _histogram = hist);
  }

  void _selectCategory(ToolCategory cat) {
    setState(() {
      _category = cat;
      final params = AdjustParam.forCategory(cat);
      if (params.isNotEmpty && !params.contains(_activeParam)) {
        _activeParam = params.first;
      }
    });
  }

  void _applyStock(FilmStockInfo stock) {
    final next = _preset.copyWith(
      stockId: stock.id,
      dirCouplers: stock.defaultDirCouplers,
      halation: stock.defaultHalation,
      grain: stock.defaultGrain,
    );
    setState(() => _preset = next);
    _pushHistory(next);
    _onLookMutated(immediateHist: true);
  }

  void _applyPaper(PrintPaperInfo paper) {
    final next = _preset.copyWith(paperId: paper.id);
    setState(() => _preset = next);
    _pushHistory(next);
    _onLookMutated(immediateHist: true);
  }

  void _applyPreset(LookPreset p) {
    final next = p.copyWith();
    setState(() => _preset = next);
    _pushHistory(next);
    _onLookMutated(immediateHist: true);
  }

  void _updateSliderLive(int newValue) {
    if (newValue == _preset.valueOf(_activeParam)) return;
    setState(() {
      _preset = _preset.withParam(_activeParam, newValue);
    });
    _onLookMutated(immediateHist: false);
  }

  void _commitSlider() {
    _pushHistory(_preset);
    _updateSampleHistogramNow();
  }

  void _resetActiveParam() {
    final next = _preset.withParam(_activeParam, 0);
    setState(() => _preset = next);
    _pushHistory(next);
    _onLookMutated(immediateHist: true);
  }

  Future<void> _exportPhoto() async {
    if (_exporting) return;
    setState(() => _exporting = true);
    try {
      final res = await widget.bridge.exportGradedPhoto(
        _liveFile.dcfName,
        _preset,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            res.localPath.isNotEmpty
                ? 'Saved ${res.localPath} (${res.elapsedMs} ms)'
                : 'Graded photo exported.',
          ),
        ),
      );
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _exporting = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Export failed: $e')),
      );
    }
  }

  Future<void> _promptSavePreset() async {
    final nameCtrl = TextEditingController(
      text: _preset.builtIn ? '${_preset.name} Custom' : _preset.name,
    );
    bool setAuto = true;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('Save SpektraFilm Preset'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameCtrl,
                autofocus: true,
                decoration: const InputDecoration(
                  labelText: 'Preset Name',
                  hintText: 'e.g. Warm Street 400',
                ),
              ),
              const SizedBox(height: 12),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Auto-apply to all incoming camera photos'),
                value: setAuto,
                onChanged: (v) => setDialogState(() => setAuto = v ?? true),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;

    final customName =
        nameCtrl.text.trim().isEmpty ? 'Custom Look' : nameCtrl.text.trim();
    final newId = _preset.builtIn
        ? 'custom_${DateTime.now().millisecondsSinceEpoch}'
        : _preset.id;
    final toSave = _preset.copyWith(
      id: newId,
      name: customName,
      builtIn: false,
    );

    var nextCatalog = await widget.bridge.saveLookPreset(toSave);
    if (setAuto) {
      nextCatalog = await widget.bridge.setAutoApplyPreset(toSave.id);
    }
    if (!mounted) return;
    setState(() {
      _catalog = nextCatalog;
      _preset = toSave;
    });
    _pushHistory(toSave);
    widget.onCatalogChanged?.call(nextCatalog);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          setAuto
              ? 'Saved "$customName" and enabled Auto-Apply for new photos.'
              : 'Saved preset "$customName".',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bgCanvas,
      body: SafeArea(
        child: Column(
          children: [
            _buildTopBar(),
            Expanded(child: _buildCanvas()),
            _buildBottomDock(),
          ],
        ),
      ),
    );
  }

  Widget _buildTopBar() {
    final bool trueRaw = _rawSession?.isTrueRaw ?? false;
    final badgeColor = _liveFile.isPreview
        ? const Color(0xFFF59E0B)
        : (_liveFile.isRaw ? _accent : const Color(0xFF14B8A6));
    final badgeText = _liveFile.isPreview
        ? 'PREVIEW • RAW...'
        : (_liveFile.isRaw ? (trueRaw ? '14-BIT RAW' : 'RAW') : 'JPG');

    return Container(
      color: _bgPanel,
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
      child: Row(
        children: [
          IconButton(
            tooltip: 'Cancel',
            onPressed: _exporting ? null : () => Navigator.pop(context),
            icon: const Icon(Icons.close, color: Colors.white70, size: 22),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  _liveFile.dcfName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(
                        color: badgeColor.withValues(alpha: 0.16),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: badgeColor.withValues(alpha: 0.55)),
                      ),
                      child: Text(
                        badgeText,
                        style: TextStyle(
                          color: badgeColor,
                          fontSize: 9.5,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.4,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        _stockDisplayName(_preset.stockId),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white54, fontSize: 11),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          IconButton(
            tooltip: 'Undo',
            onPressed: _canUndo ? _undo : null,
            icon: Icon(
              Icons.undo,
              size: 20,
              color: _canUndo ? Colors.white : Colors.white24,
            ),
          ),
          IconButton(
            tooltip: 'Redo',
            onPressed: _canRedo ? _redo : null,
            icon: Icon(
              Icons.redo,
              size: 20,
              color: _canRedo ? Colors.white : Colors.white24,
            ),
          ),
          IconButton(
            tooltip: 'Histogram',
            onPressed: () {
              setState(() => _showHistogram = !_showHistogram);
              if (_showHistogram) _updateSampleHistogramNow();
            },
            icon: Icon(
              Icons.bar_chart,
              size: 20,
              color: _showHistogram ? _accent : Colors.white60,
            ),
          ),
          IconButton(
            tooltip: 'Split Before / After',
            onPressed: () {
              setState(() => _splitCompare = !_splitCompare);
              _syncShaderNotifier();
            },
            icon: Icon(
              Icons.vertical_split,
              size: 20,
              color: _splitCompare ? _accent : Colors.white60,
            ),
          ),
          Listener(
            onPointerDown: (_) {
              setState(() => _comparingOriginal = true);
              _syncShaderNotifier();
            },
            onPointerUp: (_) {
              setState(() => _comparingOriginal = false);
              _syncShaderNotifier();
            },
            onPointerCancel: (_) {
              setState(() => _comparingOriginal = false);
              _syncShaderNotifier();
            },
            child: Tooltip(
              message: 'Hold for Original',
              child: Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: _comparingOriginal
                      ? _accent.withValues(alpha: 0.25)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Icon(
                  Icons.compare,
                  size: 20,
                  color: _comparingOriginal ? _accent : Colors.white70,
                ),
              ),
            ),
          ),
          const SizedBox(width: 4),
          FilledButton(
            onPressed: _exporting ? null : _exportPhoto,
            style: FilledButton.styleFrom(
              backgroundColor: _accent,
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              minimumSize: const Size(52, 32),
              visualDensity: VisualDensity.compact,
            ),
            child: const Text(
              'Export',
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCanvas() {
    final hasGpu = _gpuImage != null && _shaderProgram != null;
    final shownBytes = _comparingOriginal ? _originalBytes : _previewBytes;

    return Container(
      color: _bgCanvas,
      width: double.infinity,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (_loading)
            const Center(child: CircularProgressIndicator())
          else if (hasGpu)
            _buildGpuCanvas(_gpuImage!, _shaderProgram!)
          else if (shownBytes == null)
            const Center(
              child: Icon(
                Icons.image_not_supported_outlined,
                color: Colors.white38,
                size: 56,
              ),
            )
          else if (_splitCompare && _originalBytes != null && _previewBytes != null)
            _buildFallbackSplitCompare(_originalBytes!, _previewBytes!)
          else
            InteractiveViewer(
              minScale: 1.0,
              maxScale: 4.5,
              child: Padding(
                padding: const EdgeInsets.all(10),
                child: Center(
                  child: Image.memory(
                    shownBytes,
                    fit: BoxFit.contain,
                    gaplessPlayback: true,
                  ),
                ),
              ),
            ),
          if (_showHistogram && _histogram != null && !_loading)
            Positioned(
              top: 10,
              right: 12,
              child: IgnorePointer(
                child: Container(
                  width: 148,
                  height: 58,
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.72),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.white12),
                  ),
                  child: CustomPaint(
                    painter: _HistogramPainter(_histogram!),
                  ),
                ),
              ),
            ),
          if (_comparingOriginal)
            Positioned(
              top: 12,
              left: 0,
              right: 0,
              child: IgnorePointer(
                child: Center(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.75),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: _accent.withValues(alpha: 0.7)),
                    ),
                    child: Text(
                      (_rawSession?.isTrueRaw ?? false)
                          ? 'ORIGINAL 14-BIT RAW'
                          : 'ORIGINAL',
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1.0,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          if (_exporting)
            const Positioned(
              top: 12,
              left: 14,
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildGpuCanvas(ui.Image image, ui.FragmentProgram program) {
    final aspect = image.width / math.max(1, image.height);
    Widget painted = AspectRatio(
      aspectRatio: aspect,
      child: RepaintBoundary(
        child: CustomPaint(
          painter: _SpektraShaderPainter(
            program: program,
            image: image,
            stateNotifier: _shaderState,
          ),
        ),
      ),
    );

    if (_splitCompare) {
      return Padding(
        padding: const EdgeInsets.all(10),
        child: Center(
          child: AspectRatio(
            aspectRatio: aspect,
            child: LayoutBuilder(
              builder: (ctx, constraints) {
                final w = constraints.maxWidth;
                return GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onHorizontalDragUpdate: (d) {
                    if (w <= 0) return;
                    _splitPos = (_splitPos + d.delta.dx / w).clamp(0.06, 0.94);
                    _syncShaderNotifier();
                    setState(() {});
                  },
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      painted,
                      Positioned(
                        left: w * _splitPos - 1,
                        top: 0,
                        bottom: 0,
                        child: Container(width: 2, color: Colors.white),
                      ),
                      Positioned(
                        left: (w * _splitPos - 14).clamp(0.0, math.max(0.0, w - 28)),
                        top: constraints.maxHeight * 0.5 - 14,
                        child: Container(
                          width: 28,
                          height: 28,
                          decoration: BoxDecoration(
                            color: _accent,
                            shape: BoxShape.circle,
                            border: Border.all(color: Colors.white, width: 2),
                          ),
                          child: const Icon(Icons.drag_handle, size: 16, color: Colors.white),
                        ),
                      ),
                      Positioned(bottom: 10, left: 10, child: _splitTag('ORIGINAL')),
                      Positioned(bottom: 10, right: 10, child: _splitTag('SPEKTRAFILM')),
                    ],
                  ),
                );
              },
            ),
          ),
        ),
      );
    }

    return InteractiveViewer(
      minScale: 1.0,
      maxScale: 5.0,
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Center(child: painted),
      ),
    );
  }

  Widget _buildFallbackSplitCompare(Uint8List original, Uint8List graded) {
    return LayoutBuilder(
      builder: (ctx, constraints) {
        final w = constraints.maxWidth;
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onHorizontalDragUpdate: (d) {
            if (w <= 0) return;
            setState(() {
              _splitPos = (_splitPos + d.delta.dx / w).clamp(0.08, 0.92);
            });
          },
          child: Stack(
            fit: StackFit.expand,
            children: [
              Padding(
                padding: const EdgeInsets.all(10),
                child: Center(
                  child: Image.memory(graded, fit: BoxFit.contain, gaplessPlayback: true),
                ),
              ),
              ClipRect(
                clipper: _LeftSplitClipper(_splitPos),
                child: Padding(
                  padding: const EdgeInsets.all(10),
                  child: Center(
                    child: Image.memory(original, fit: BoxFit.contain, gaplessPlayback: true),
                  ),
                ),
              ),
              Positioned(
                left: w * _splitPos - 1,
                top: 0,
                bottom: 0,
                child: Container(width: 2, color: Colors.white),
              ),
              Positioned(
                left: (w * _splitPos - 14).clamp(0.0, w - 28),
                top: constraints.maxHeight * 0.5 - 14,
                child: Container(
                  width: 28,
                  height: 28,
                  decoration: BoxDecoration(
                    color: _accent,
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.white, width: 2),
                  ),
                  child: const Icon(Icons.drag_handle, size: 16, color: Colors.white),
                ),
              ),
              Positioned(bottom: 12, left: 14, child: _splitTag('ORIGINAL')),
              Positioned(bottom: 12, right: 14, child: _splitTag('SPEKTRAFILM')),
            ],
          ),
        );
      },
    );
  }

  Widget _splitTag(String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.7),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        text,
        style: const TextStyle(
          color: Colors.white70,
          fontSize: 10,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.7,
        ),
      ),
    );
  }

  Widget _buildBottomDock() {
    return Container(
      decoration: const BoxDecoration(
        color: _bgPanel,
        border: Border(top: BorderSide(color: Color(0xFF232936))),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_category == ToolCategory.presets)
            _buildPresetsDock()
          else if (_category == ToolCategory.film)
            _buildFilmDock()
          else
            _buildParamDock(_category),
          const Divider(height: 1, color: Color(0xFF232936)),
          SizedBox(
            height: 54,
            child: Row(
              children: [
                for (final cat in ToolCategory.values)
                  Expanded(
                    child: InkWell(
                      onTap: () => _selectCategory(cat),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            _categoryIcon(cat),
                            size: 19,
                            color: cat == _category ? _accent : Colors.white54,
                          ),
                          const SizedBox(height: 2),
                          Text(
                            cat.label,
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: cat == _category
                                  ? FontWeight.w600
                                  : FontWeight.w400,
                              color: cat == _category ? _accent : Colors.white60,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPresetsDock() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                'SpektraFilm Presets',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextButton.icon(
                    onPressed: _resetAll,
                    icon: const Icon(Icons.restart_alt, size: 16),
                    label: const Text('Reset'),
                    style: TextButton.styleFrom(
                      foregroundColor: Colors.white60,
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  const SizedBox(width: 4),
                  FilledButton.icon(
                    onPressed: _promptSavePreset,
                    icon: const Icon(Icons.auto_awesome, size: 15),
                    label: const Text('Save Preset'),
                    style: FilledButton.styleFrom(
                      backgroundColor: _accent,
                      foregroundColor: Colors.white,
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: 62,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: _catalog.presets.length,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (ctx, idx) {
                final p = _catalog.presets[idx];
                final active = p.id == _preset.id;
                final isAuto = p.id == _catalog.autoApplyPresetId;
                return InkWell(
                  onTap: () => _applyPreset(p),
                  borderRadius: BorderRadius.circular(10),
                  child: Container(
                    width: 142,
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: active ? _accent.withValues(alpha: 0.18) : _bgChip,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: active ? _accent : Colors.white12,
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                p.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  color: active ? Colors.white : Colors.white70,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                            if (isAuto)
                              const Icon(
                                Icons.auto_awesome,
                                size: 13,
                                color: _accent,
                              ),
                          ],
                        ),
                        const SizedBox(height: 2),
                        Text(
                          _stockDisplayName(p.stockId).replaceFirst('Kodak ', ''),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: Colors.white54, fontSize: 10.5),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilmDock() {
    final filmParams = AdjustParam.forCategory(ToolCategory.film);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 36,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: _catalog.stocks.length,
              separatorBuilder: (_, _) => const SizedBox(width: 6),
              itemBuilder: (ctx, idx) {
                final stock = _catalog.stocks[idx];
                final selected = stock.id == _preset.stockId;
                return ChoiceChip(
                  label: Text(
                    stock.name.replaceFirst('Kodak ', ''),
                    style: const TextStyle(fontSize: 11.5),
                  ),
                  selected: selected,
                  selectedColor: _accent,
                  backgroundColor: _bgChip,
                  side: BorderSide.none,
                  visualDensity: VisualDensity.compact,
                  onSelected: (_) => _applyStock(stock),
                );
              },
            ),
          ),
          const SizedBox(height: 6),
          SizedBox(
            height: 34,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: _catalog.papers.length,
              separatorBuilder: (_, _) => const SizedBox(width: 6),
              itemBuilder: (ctx, idx) {
                final paper = _catalog.papers[idx];
                final selected = paper.id == _preset.paperId;
                return ChoiceChip(
                  label: Text(
                    paper.name,
                    style: const TextStyle(fontSize: 11),
                  ),
                  selected: selected,
                  selectedColor: const Color(0xFF2563EB),
                  backgroundColor: _bgChip,
                  side: BorderSide.none,
                  visualDensity: VisualDensity.compact,
                  onSelected: (_) => _applyPaper(paper),
                );
              },
            ),
          ),
          const SizedBox(height: 6),
          _buildParamSelectorAndSlider(filmParams),
        ],
      ),
    );
  }

  Widget _buildParamDock(ToolCategory category) {
    final params = AdjustParam.forCategory(category);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: _buildParamSelectorAndSlider(params),
    );
  }

  Widget _buildParamSelectorAndSlider(List<AdjustParam> params) {
    final val = _preset.valueOf(_activeParam);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          height: 34,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: params.length,
            separatorBuilder: (_, _) => const SizedBox(width: 6),
            itemBuilder: (ctx, idx) {
              final p = params[idx];
              final selected = p == _activeParam;
              final pVal = _preset.valueOf(p);
              return ChoiceChip(
                label: Text(
                  pVal == 0 ? p.label : '${p.label} ${p.formatValue(pVal)}',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: selected ? Colors.white : Colors.white70,
                    fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                  ),
                ),
                selected: selected,
                selectedColor: _accent,
                backgroundColor: _bgChip,
                side: BorderSide.none,
                visualDensity: VisualDensity.compact,
                onSelected: (_) => setState(() => _activeParam = p),
              );
            },
          ),
        ),
        const SizedBox(height: 6),
        Row(
          children: [
            Text(
              '${_activeParam.label} ${_activeParam.formatValue(val)}',
              style: const TextStyle(
                color: Colors.white,
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
            const Spacer(),
            GestureDetector(
              onTap: _resetActiveParam,
              child: const Text(
                'Reset 0',
                style: TextStyle(color: Colors.white54, fontSize: 11),
              ),
            ),
          ],
        ),
        GestureDetector(
          onDoubleTap: _resetActiveParam,
          child: SizedBox(
            height: 36,
            child: Stack(
              alignment: Alignment.center,
              children: [
                Positioned.fill(
                  child: IgnorePointer(
                    child: CustomPaint(
                      painter: _RulerTickPainter(isBipolar: _activeParam.isBipolar),
                    ),
                  ),
                ),
                SliderTheme(
                  data: SliderTheme.of(context).copyWith(
                    activeTrackColor: _accent,
                    inactiveTrackColor: Colors.white24,
                    thumbColor: Colors.white,
                    overlayColor: _accent.withValues(alpha: 0.2),
                    trackHeight: 2.5,
                    thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 7),
                  ),
                  child: Slider(
                    min: _activeParam.min.toDouble(),
                    max: _activeParam.max.toDouble(),
                    value: val.toDouble(),
                    onChanged: (d) => _updateSliderLive(d.round()),
                    onChangeEnd: (_) => _commitSlider(),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  IconData _categoryIcon(ToolCategory cat) {
    switch (cat) {
      case ToolCategory.presets:
        return Icons.auto_awesome_mosaic;
      case ToolCategory.film:
        return Icons.camera_roll_outlined;
      case ToolCategory.light:
        return Icons.wb_sunny_outlined;
      case ToolCategory.color:
        return Icons.palette_outlined;
      case ToolCategory.detail:
        return Icons.tune;
    }
  }

  String _stockDisplayName(String stockId) {
    for (final s in _catalog.stocks) {
      if (s.id == stockId) return s.name;
    }
    return stockId;
  }
}

class _SpektraShaderPainter extends CustomPainter {
  _SpektraShaderPainter({
    required this.program,
    required this.image,
    required this.stateNotifier,
  }) : super(repaint: stateNotifier);

  final ui.FragmentProgram program;
  final ui.Image image;
  final ValueNotifier<_ShaderRenderState> stateNotifier;

  @override
  void paint(Canvas canvas, Size size) {
    final s = stateNotifier.value;
    final p = s.preset;
    final stock = FilmStockShaderSpec.of(p.stockId);
    final paper = PrintPaperShaderSpec.of(p.paperId);
    final shader = program.fragmentShader();

    int idx = 0;
    shader.setFloat(idx++, size.width);
    shader.setFloat(idx++, size.height);
    shader.setFloat(idx++, image.width.toDouble());
    shader.setFloat(idx++, image.height.toDouble());
    shader.setFloat(idx++, s.isLog2 ? 1.0 : 0.0);
    shader.setFloat(idx++, s.splitMode.toDouble());
    shader.setFloat(idx++, s.splitPos);

    shader.setFloat(idx++, p.exposure * 2.0 / 100.0);
    shader.setFloat(idx++, p.contrast / 100.0);
    shader.setFloat(idx++, p.highlights / 100.0);
    shader.setFloat(idx++, p.shadows / 100.0);
    shader.setFloat(idx++, p.whites / 100.0);
    shader.setFloat(idx++, p.blacks / 100.0);
    shader.setFloat(idx++, p.warmth / 100.0);
    shader.setFloat(idx++, p.tint / 100.0);
    shader.setFloat(idx++, p.vibrance / 100.0);
    shader.setFloat(idx++, p.saturation / 100.0);
    shader.setFloat(idx++, p.dirCouplers / 100.0);
    shader.setFloat(idx++, p.halation / 100.0);
    shader.setFloat(idx++, p.grain / 100.0);
    shader.setFloat(idx++, p.fade / 100.0);
    shader.setFloat(idx++, p.structure / 100.0);
    shader.setFloat(idx++, p.clarity / 100.0);
    shader.setFloat(idx++, p.sharpen / 100.0);
    shader.setFloat(idx++, p.vignette / 100.0);

    shader.setFloat(idx++, stock.id == 'neutral' ? 1.0 : 0.0);
    shader.setFloat(idx++, stock.isMono ? 1.0 : 0.0);
    for (final mv in stock.matrix) {
      shader.setFloat(idx++, mv);
    }
    shader.setFloat(idx++, stock.gammaR);
    shader.setFloat(idx++, stock.gammaG);
    shader.setFloat(idx++, stock.gammaB);
    shader.setFloat(idx++, stock.toe);
    shader.setFloat(idx++, stock.shoulder);
    for (final dv in stock.dirOffDiag) {
      shader.setFloat(idx++, dv);
    }
    shader.setFloat(idx++, stock.chromaGain);
    shader.setFloat(idx++, stock.grainSize);

    shader.setFloat(idx++, paper.contrast);
    shader.setFloat(idx++, paper.dMinR);
    shader.setFloat(idx++, paper.dMinG);
    shader.setFloat(idx++, paper.dMinB);
    shader.setFloat(idx++, paper.gainR);
    shader.setFloat(idx++, paper.gainG);
    shader.setFloat(idx++, paper.gainB);

    shader.setImageSampler(0, image);

    final paint = Paint()..shader = shader;
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), paint);
  }

  @override
  bool shouldRepaint(covariant _SpektraShaderPainter old) =>
      old.image != image || old.program != program;
}

HistogramData _computeFastSampleHistogram(RawEditorSession session, LookPreset p) {
  const bins = 64;
  final rCnt = List<int>.filled(bins, 0);
  final gCnt = List<int>.filled(bins, 0);
  final bCnt = List<int>.filled(bins, 0);
  final lCnt = List<int>.filled(bins, 0);

  final rgb = session.histRgb;
  if (rgb.length < 3) {
    return HistogramData(r: rCnt, g: gCnt, b: bCnt, l: lCnt);
  }

  final stock = FilmStockShaderSpec.of(p.stockId);
  final paper = PrintPaperShaderSpec.of(p.paperId);
  final bypass = stock.id == 'neutral';

  final evGain = math.pow(2.0, p.exposure * 2.0 / 100.0).toDouble();
  final warmth = p.warmth / 100.0;
  final tint = p.tint / 100.0;
  final wbR = evGain * (1.0 + warmth * 0.28 + tint * 0.08).clamp(0.45, 2.2);
  final wbG = evGain * (1.0 - warmth.abs() * 0.03 - tint * 0.16).clamp(0.45, 2.2);
  final wbB = evGain * (1.0 - warmth * 0.28 + tint * 0.08).clamp(0.45, 2.2);

  final hi = p.highlights / 100.0;
  final sh = p.shadows / 100.0;
  final wh = p.whites / 100.0;
  final bl = p.blacks / 100.0;
  final vib = p.vibrance / 100.0;
  final fadeLift = (p.fade / 100.0) * 0.085;
  final dirK = (p.dirCouplers / 100.0) * 0.65;
  final paperC = paper.contrast * (1.0 + p.contrast * 0.45 / 100.0);
  final satMul = stock.isMono
      ? 0.0
      : (stock.chromaGain * (1.0 + p.saturation * 0.70 / 100.0)).clamp(0.0, 2.2);

  double decodeByte(int b) {
    final c = b / 255.0;
    if (session.isLog2) {
      return (math.pow(2.0, c * 8.0) - 1.0) / 16.0;
    }
    return c <= 0.04045 ? (c / 12.92) : math.pow((c + 0.055) / 1.055, 2.4).toDouble();
  }

  double hd(double x, double g) {
    if (x <= 0.0001) return 0.0;
    final boosted = x * (1.0 + stock.shoulder * 0.6);
    final comp = boosted / (boosted + 0.42 + stock.shoulder * 0.55);
    final mid = (comp * (1.42 + stock.shoulder * 0.55)).clamp(0.0, 1.35);
    final curved = (g - 1.0).abs() > 0.005 ? math.pow(mid, g).toDouble() : mid;
    return (curved + stock.toe * 0.025 * (1.0 - math.min(1.0, curved))).clamp(0.0, 1.35);
  }

  double paperMap(double v, double c, double g, double dMin) {
    final cc = c.clamp(0.55, 1.75);
    const pivot = 0.22;
    final x = v > 0.0001 ? pivot * math.pow(v / pivot, cc).toDouble() : 0.0;
    final soft = (x / (x + 0.85)) * 1.85;
    return (soft * g + dMin).clamp(0.0, 1.25);
  }

  int toBin(double lin) {
    final c = lin.clamp(0.0, 1.0);
    final s = c <= 0.0031308 ? 12.92 * c : (1.055 * math.pow(c, 1.0 / 2.4) - 0.055);
    return (s * 63.0 + 0.5).toInt().clamp(0, 63);
  }

  final m = stock.matrix;
  final dm = stock.dirOffDiag;
  for (var i = 0; i + 2 < rgb.length; i += 6) {
    var r = decodeByte(rgb[i]) * wbR;
    var g = decodeByte(rgb[i + 1]) * wbG;
    var b = decodeByte(rgb[i + 2]) * wbB;

    if (session.isLog2 && (hi < -0.01 || wh < -0.01)) {
      final rawLum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      if (rawLum > 0.70) {
        final pull = (-hi * 0.55 - wh * 0.40).clamp(0.0, 0.95);
        final knee = 0.70 + (rawLum - 0.70) / (1.0 + pull * 2.4 * (rawLum - 0.70));
        final ks = knee / rawLum;
        r *= ks;
        g *= ks;
        b *= ks;
      }
    }

    if (!bypass) {
      final sr = math.max(0.0, m[0] * r + m[1] * g + m[2] * b);
      final sg = math.max(0.0, m[3] * r + m[4] * g + m[5] * b);
      final sb = math.max(0.0, m[6] * r + m[7] * g + m[8] * b);
      var dr = hd(sr, stock.gammaR);
      var dg = hd(sg, stock.gammaG);
      var db = hd(sb, stock.gammaB);
      if (dirK > 0.001 && !stock.isMono) {
        final inhR = dm[0] * dg + dm[1] * db;
        final inhG = dm[2] * dr + dm[3] * db;
        final inhB = dm[4] * dr + dm[5] * dg;
        dr = (dr - dirK * (inhR - dr * 0.25)).clamp(0.0, 1.35);
        dg = (dg - dirK * (inhG - dg * 0.25)).clamp(0.0, 1.35);
        db = (db - dirK * (inhB - db * 0.25)).clamp(0.0, 1.35);
      }
      r = paperMap(dr, paperC, paper.gainR, paper.dMinR);
      g = paperMap(dg, paperC, paper.gainG, paper.dMinG);
      b = paperMap(db, paperC, paper.gainB, paper.dMinB);
    } else {
      final nc = 1.0 + p.contrast * 0.45 / 100.0;
      r = paperMap(r.clamp(0.0, 1.35), nc, 1.0, 0.0);
      g = paperMap(g.clamp(0.0, 1.35), nc, 1.0, 0.0);
      b = paperMap(b.clamp(0.0, 1.35), nc, 1.0, 0.0);
    }

    final lum = (0.2126 * r + 0.7152 * g + 0.0722 * b).clamp(0.0, 1.5);
    if (lum > 0.0005 && (hi.abs() > 0.005 || sh.abs() > 0.005 || wh.abs() > 0.005 || bl.abs() > 0.005)) {
      final t = lum.clamp(0.0, 1.0);
      final omt = 1.0 - t;
      final shM = omt * omt;
      final hiM = t * t;
      final target = math.max(0.0, lum + sh * 0.24 * shM + hi * 0.22 * hiM + bl * 0.16 * shM * shM + wh * 0.20 * hiM * t);
      final sc = (target / lum).clamp(0.28, 2.8);
      r *= sc;
      g *= sc;
      b *= sc;
    }

    final postLum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    var effSat = satMul;
    if (!stock.isMono && vib.abs() > 0.005) {
      final maxC = math.max(r, math.max(g, b));
      final minC = math.min(r, math.min(g, b));
      final satEst = maxC > 0.001 ? ((maxC - minC) / maxC).clamp(0.0, 1.0) : 0.0;
      final skin = (r > g && g > b) ? 0.55 : 1.0;
      effSat = (satMul * (1.0 + vib * 0.65 * (1.0 - satEst) * skin)).clamp(0.0, 2.4);
    }
    if (effSat != 1.0) {
      r = postLum + (r - postLum) * effSat;
      g = postLum + (g - postLum) * effSat;
      b = postLum + (b - postLum) * effSat;
    }
    if (fadeLift > 0.0) {
      final comp = 1.0 - fadeLift;
      r = r * comp + fadeLift * (1.0 + paper.dMinR * 4.0);
      g = g * comp + fadeLift * (1.0 + paper.dMinG * 4.0);
      b = b * comp + fadeLift * (1.0 + paper.dMinB * 4.0);
    }

    final ri = toBin(r);
    final gi = toBin(g);
    final bi = toBin(b);
    final li = toBin(0.2126 * r.clamp(0.0, 1.0) + 0.7152 * g.clamp(0.0, 1.0) + 0.0722 * b.clamp(0.0, 1.0));
    rCnt[ri]++;
    gCnt[gi]++;
    bCnt[bi]++;
    lCnt[li]++;
  }

  int peak = 1;
  for (var i = 1; i < bins - 1; i++) {
    if (rCnt[i] > peak) peak = rCnt[i];
    if (gCnt[i] > peak) peak = gCnt[i];
    if (bCnt[i] > peak) peak = bCnt[i];
    if (lCnt[i] > peak) peak = lCnt[i];
  }
  for (var i = 0; i < bins; i++) {
    rCnt[i] = ((rCnt[i] * 255) ~/ peak).clamp(0, 255);
    gCnt[i] = ((gCnt[i] * 255) ~/ peak).clamp(0, 255);
    bCnt[i] = ((bCnt[i] * 255) ~/ peak).clamp(0, 255);
    lCnt[i] = ((lCnt[i] * 255) ~/ peak).clamp(0, 255);
  }
  return HistogramData(r: rCnt, g: gCnt, b: bCnt, l: lCnt);
}

class _LeftSplitClipper extends CustomClipper<Rect> {
  const _LeftSplitClipper(this.fraction);
  final double fraction;

  @override
  Rect getClip(Size size) =>
      Rect.fromLTRB(0, 0, size.width * fraction.clamp(0.0, 1.0), size.height);

  @override
  bool shouldReclip(covariant _LeftSplitClipper old) => old.fraction != fraction;
}

class _HistogramPainter extends CustomPainter {
  const _HistogramPainter(this.hist);
  final HistogramData hist;

  @override
  void paint(Canvas canvas, Size size) {
    void drawChannel(List<int> bins, Color color) {
      if (bins.length < 2) return;
      final path = Path()..moveTo(0, size.height);
      final dx = size.width / (bins.length - 1);
      for (var i = 0; i < bins.length; i++) {
        final y = size.height - (bins[i] / 255.0) * (size.height - 2);
        path.lineTo(i * dx, y);
      }
      path.lineTo(size.width, size.height);
      path.close();
      canvas.drawPath(
        path,
        Paint()
          ..color = color
          ..style = PaintingStyle.fill,
      );
    }

    drawChannel(hist.r, const Color(0x77EF4444));
    drawChannel(hist.g, const Color(0x7722C55E));
    drawChannel(hist.b, const Color(0x773B82F6));
    drawChannel(hist.l, const Color(0x55FFFFFF));
  }

  @override
  bool shouldRepaint(covariant _HistogramPainter old) => old.hist != hist;
}

class _RulerTickPainter extends CustomPainter {
  const _RulerTickPainter({required this.isBipolar});
  final bool isBipolar;

  @override
  void paint(Canvas canvas, Size size) {
    final tickPaint = Paint()
      ..color = Colors.white24
      ..strokeWidth = 1;
    const count = 20;
    const pad = 20.0;
    final usable = (size.width - pad * 2).clamp(20.0, size.width);
    for (var i = 0; i <= count; i++) {
      final x = pad + (usable * i / count);
      final isCenter = isBipolar && i == count ~/ 2;
      final h = isCenter ? 10.0 : (i % 5 == 0 ? 6.0 : 3.5);
      canvas.drawLine(
        Offset(x, size.height - h - 2),
        Offset(x, size.height - 2),
        isCenter ? (Paint()..color = const Color(0xFFFF6D00)..strokeWidth = 1.5) : tickPaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _RulerTickPainter old) => old.isBipolar != isBipolar;
}
