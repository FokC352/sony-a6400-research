import 'dart:typed_data';

enum ToolCategory {
  presets('Presets'),
  film('Film'),
  light('Light'),
  color('Color'),
  detail('Detail');

  const ToolCategory(this.label);
  final String label;
}

enum AdjustParam {
  exposure('Exposure', -100, 100, ToolCategory.light),
  contrast('Contrast', -100, 100, ToolCategory.light),
  highlights('Highlights', -100, 100, ToolCategory.light),
  shadows('Shadows', -100, 100, ToolCategory.light),
  whites('Whites', -100, 100, ToolCategory.light),
  blacks('Blacks', -100, 100, ToolCategory.light),
  warmth('Temp', -100, 100, ToolCategory.color),
  tint('Tint', -100, 100, ToolCategory.color),
  vibrance('Vibrance', -100, 100, ToolCategory.color),
  saturation('Saturation', -100, 100, ToolCategory.color),
  dirCouplers('DIR Couplers', 0, 100, ToolCategory.film),
  halation('Halation', 0, 100, ToolCategory.film),
  grain('Grain', 0, 100, ToolCategory.film),
  fade('Fade', 0, 100, ToolCategory.film),
  structure('Structure', -100, 100, ToolCategory.detail),
  clarity('Clarity', -100, 100, ToolCategory.detail),
  sharpen('Sharpen', 0, 100, ToolCategory.detail),
  vignette('Vignette', 0, 100, ToolCategory.detail);

  const AdjustParam(this.label, this.min, this.max, this.category);

  final String label;
  final int min;
  final int max;
  final ToolCategory category;

  bool get isBipolar => min < 0;

  String formatValue(int value) {
    if (isBipolar && value > 0) return '+$value';
    return '$value';
  }

  static List<AdjustParam> forCategory(ToolCategory cat) =>
      values.where((p) => p.category == cat).toList(growable: false);
}

class HistogramData {
  const HistogramData({
    required this.r,
    required this.g,
    required this.b,
    required this.l,
  });

  final List<int> r;
  final List<int> g;
  final List<int> b;
  final List<int> l;

  factory HistogramData.fromMap(Map<dynamic, dynamic> map) {
    List<int> parseChannel(String key) {
      final raw = map[key];
      if (raw is List) {
        return raw.map((e) => (e as num).toInt().clamp(0, 255)).toList(growable: false);
      }
      return const [];
    }

    return HistogramData(
      r: parseChannel('r'),
      g: parseChannel('g'),
      b: parseChannel('b'),
      l: parseChannel('l'),
    );
  }
}

class PreviewRenderResult {
  const PreviewRenderResult({
    required this.jpegBytes,
    this.histogram,
  });

  final Uint8List jpegBytes;
  final HistogramData? histogram;
}

class FilmStockInfo {
  const FilmStockInfo({
    required this.id,
    required this.name,
    required this.subtitle,
    this.defaultHalation = 20,
    this.defaultGrain = 25,
    this.defaultDirCouplers = 50,
  });

  final String id;
  final String name;
  final String subtitle;
  final int defaultHalation;
  final int defaultGrain;
  final int defaultDirCouplers;

  factory FilmStockInfo.fromMap(Map<dynamic, dynamic> map) {
    return FilmStockInfo(
      id: (map['id'] as String?) ?? 'portra_400',
      name: (map['name'] as String?) ?? 'Film Stock',
      subtitle: (map['subtitle'] as String?) ?? '',
      defaultHalation: (map['defaultHalation'] as num?)?.toInt() ?? 20,
      defaultGrain: (map['defaultGrain'] as num?)?.toInt() ?? 25,
      defaultDirCouplers: (map['defaultDirCouplers'] as num?)?.toInt() ?? 50,
    );
  }
}

class PrintPaperInfo {
  const PrintPaperInfo({
    required this.id,
    required this.name,
    required this.subtitle,
  });

  final String id;
  final String name;
  final String subtitle;

  factory PrintPaperInfo.fromMap(Map<dynamic, dynamic> map) {
    return PrintPaperInfo(
      id: (map['id'] as String?) ?? 'endura',
      name: (map['name'] as String?) ?? 'Print Paper',
      subtitle: (map['subtitle'] as String?) ?? '',
    );
  }
}

class LookPreset {
  const LookPreset({
    required this.id,
    required this.name,
    this.stockId = 'portra_400',
    this.paperId = 'endura',
    this.builtIn = false,
    this.exposure = 0,
    this.contrast = 0,
    this.structure = 0,
    this.highlights = 0,
    this.shadows = 0,
    this.warmth = 0,
    this.tint = 0,
    this.saturation = 0,
    this.dirCouplers = 50,
    this.halation = 20,
    this.grain = 25,
    this.fade = 0,
    this.vignette = 0,
    this.whites = 0,
    this.blacks = 0,
    this.vibrance = 0,
    this.clarity = 0,
    this.sharpen = 0,
  });

  final String id;
  final String name;
  final String stockId;
  final String paperId;
  final bool builtIn;
  final int exposure;
  final int contrast;
  final int structure;
  final int highlights;
  final int shadows;
  final int warmth;
  final int tint;
  final int saturation;
  final int dirCouplers;
  final int halation;
  final int grain;
  final int fade;
  final int vignette;
  final int whites;
  final int blacks;
  final int vibrance;
  final int clarity;
  final int sharpen;

  int valueOf(AdjustParam param) {
    switch (param) {
      case AdjustParam.exposure:
        return exposure;
      case AdjustParam.contrast:
        return contrast;
      case AdjustParam.structure:
        return structure;
      case AdjustParam.highlights:
        return highlights;
      case AdjustParam.shadows:
        return shadows;
      case AdjustParam.warmth:
        return warmth;
      case AdjustParam.tint:
        return tint;
      case AdjustParam.saturation:
        return saturation;
      case AdjustParam.dirCouplers:
        return dirCouplers;
      case AdjustParam.halation:
        return halation;
      case AdjustParam.grain:
        return grain;
      case AdjustParam.fade:
        return fade;
      case AdjustParam.vignette:
        return vignette;
      case AdjustParam.whites:
        return whites;
      case AdjustParam.blacks:
        return blacks;
      case AdjustParam.vibrance:
        return vibrance;
      case AdjustParam.clarity:
        return clarity;
      case AdjustParam.sharpen:
        return sharpen;
    }
  }

  LookPreset withParam(AdjustParam param, int rawValue) {
    final v = rawValue.clamp(param.min, param.max);
    switch (param) {
      case AdjustParam.exposure:
        return copyWith(exposure: v);
      case AdjustParam.contrast:
        return copyWith(contrast: v);
      case AdjustParam.structure:
        return copyWith(structure: v);
      case AdjustParam.highlights:
        return copyWith(highlights: v);
      case AdjustParam.shadows:
        return copyWith(shadows: v);
      case AdjustParam.warmth:
        return copyWith(warmth: v);
      case AdjustParam.tint:
        return copyWith(tint: v);
      case AdjustParam.saturation:
        return copyWith(saturation: v);
      case AdjustParam.dirCouplers:
        return copyWith(dirCouplers: v);
      case AdjustParam.halation:
        return copyWith(halation: v);
      case AdjustParam.grain:
        return copyWith(grain: v);
      case AdjustParam.fade:
        return copyWith(fade: v);
      case AdjustParam.vignette:
        return copyWith(vignette: v);
      case AdjustParam.whites:
        return copyWith(whites: v);
      case AdjustParam.blacks:
        return copyWith(blacks: v);
      case AdjustParam.vibrance:
        return copyWith(vibrance: v);
      case AdjustParam.clarity:
        return copyWith(clarity: v);
      case AdjustParam.sharpen:
        return copyWith(sharpen: v);
    }
  }

  LookPreset resetAdjustments({String? keepStockId, String? keepPaperId}) {
    return LookPreset(
      id: id,
      name: name,
      stockId: keepStockId ?? stockId,
      paperId: keepPaperId ?? paperId,
      builtIn: builtIn,
    );
  }

  LookPreset copyWith({
    String? id,
    String? name,
    String? stockId,
    String? paperId,
    bool? builtIn,
    int? exposure,
    int? contrast,
    int? structure,
    int? highlights,
    int? shadows,
    int? warmth,
    int? tint,
    int? saturation,
    int? dirCouplers,
    int? halation,
    int? grain,
    int? fade,
    int? vignette,
    int? whites,
    int? blacks,
    int? vibrance,
    int? clarity,
    int? sharpen,
  }) {
    return LookPreset(
      id: id ?? this.id,
      name: name ?? this.name,
      stockId: stockId ?? this.stockId,
      paperId: paperId ?? this.paperId,
      builtIn: builtIn ?? this.builtIn,
      exposure: exposure ?? this.exposure,
      contrast: contrast ?? this.contrast,
      structure: structure ?? this.structure,
      highlights: highlights ?? this.highlights,
      shadows: shadows ?? this.shadows,
      warmth: warmth ?? this.warmth,
      tint: tint ?? this.tint,
      saturation: saturation ?? this.saturation,
      dirCouplers: dirCouplers ?? this.dirCouplers,
      halation: halation ?? this.halation,
      grain: grain ?? this.grain,
      fade: fade ?? this.fade,
      vignette: vignette ?? this.vignette,
      whites: whites ?? this.whites,
      blacks: blacks ?? this.blacks,
      vibrance: vibrance ?? this.vibrance,
      clarity: clarity ?? this.clarity,
      sharpen: sharpen ?? this.sharpen,
    );
  }

  Map<String, dynamic> toMap() => {
        'id': id,
        'name': name,
        'stockId': stockId,
        'paperId': paperId,
        'builtIn': builtIn,
        'exposure': exposure,
        'contrast': contrast,
        'structure': structure,
        'highlights': highlights,
        'shadows': shadows,
        'warmth': warmth,
        'tint': tint,
        'saturation': saturation,
        'dirCouplers': dirCouplers,
        'halation': halation,
        'grain': grain,
        'fade': fade,
        'vignette': vignette,
        'whites': whites,
        'blacks': blacks,
        'vibrance': vibrance,
        'clarity': clarity,
        'sharpen': sharpen,
      };

  factory LookPreset.fromMap(Map<dynamic, dynamic> map) {
    int readInt(String k, int def) => (map[k] as num?)?.toInt() ?? def;
    return LookPreset(
      id: (map['id'] as String?) ?? 'custom',
      name: (map['name'] as String?) ?? 'Custom Film',
      stockId: (map['stockId'] as String?) ?? 'portra_400',
      paperId: (map['paperId'] as String?) ?? 'endura',
      builtIn: (map['builtIn'] as bool?) ?? false,
      exposure: readInt('exposure', 0).clamp(-100, 100),
      contrast: readInt('contrast', 0).clamp(-100, 100),
      structure: readInt('structure', 0).clamp(-100, 100),
      highlights: readInt('highlights', 0).clamp(-100, 100),
      shadows: readInt('shadows', 0).clamp(-100, 100),
      warmth: readInt('warmth', 0).clamp(-100, 100),
      tint: readInt('tint', 0).clamp(-100, 100),
      saturation: readInt('saturation', 0).clamp(-100, 100),
      dirCouplers: readInt('dirCouplers', 50).clamp(0, 100),
      halation: readInt('halation', 20).clamp(0, 100),
      grain: readInt('grain', 25).clamp(0, 100),
      fade: readInt('fade', 0).clamp(0, 100),
      vignette: readInt('vignette', 0).clamp(0, 100),
      whites: readInt('whites', 0).clamp(-100, 100),
      blacks: readInt('blacks', 0).clamp(-100, 100),
      vibrance: readInt('vibrance', 0).clamp(-100, 100),
      clarity: readInt('clarity', 0).clamp(-100, 100),
      sharpen: readInt('sharpen', 0).clamp(0, 100),
    );
  }
}

class LookCatalog {
  const LookCatalog({
    required this.stocks,
    required this.papers,
    required this.presets,
    this.autoApplyPresetId,
  });

  final List<FilmStockInfo> stocks;
  final List<PrintPaperInfo> papers;
  final List<LookPreset> presets;
  final String? autoApplyPresetId;

  LookPreset? findPreset(String? id) {
    if (id == null || id.isEmpty) return null;
    for (final p in presets) {
      if (p.id == id) return p;
    }
    return null;
  }

  factory LookCatalog.fromMap(Map<dynamic, dynamic> map) {
    final rawStocks = map['stocks'] as List<dynamic>? ?? const [];
    final rawPapers = map['papers'] as List<dynamic>? ?? const [];
    final rawPresets = map['presets'] as List<dynamic>? ?? const [];
    final autoId = map['autoApplyPresetId'] as String?;
    return LookCatalog(
      stocks: rawStocks
          .whereType<Map<dynamic, dynamic>>()
          .map(FilmStockInfo.fromMap)
          .toList(growable: false),
      papers: rawPapers
          .whereType<Map<dynamic, dynamic>>()
          .map(PrintPaperInfo.fromMap)
          .toList(growable: false),
      presets: rawPresets
          .whereType<Map<dynamic, dynamic>>()
          .map(LookPreset.fromMap)
          .toList(growable: false),
      autoApplyPresetId: (autoId != null && autoId.isNotEmpty) ? autoId : null,
    );
  }

  factory LookCatalog.fallbackDefaults() => const LookCatalog(
        stocks: [
          FilmStockInfo(
            id: 'portra_400',
            name: 'Kodak Portra 400',
            subtitle: 'Natural skin tones, soft highlights',
            defaultHalation: 18,
            defaultGrain: 24,
            defaultDirCouplers: 55,
          ),
          FilmStockInfo(
            id: 'gold_200',
            name: 'Kodak Gold 200',
            subtitle: 'Golden warmth, rich mids & blues',
            defaultHalation: 26,
            defaultGrain: 34,
            defaultDirCouplers: 62,
          ),
          FilmStockInfo(
            id: 'cinestill_800t',
            name: 'CineStill 800T',
            subtitle: 'Remjet-free tungsten halation glow',
            defaultHalation: 72,
            defaultGrain: 42,
            defaultDirCouplers: 52,
          ),
          FilmStockInfo(
            id: 'hp5_400',
            name: 'Ilford HP5 Plus 400',
            subtitle: 'Classic silver-gelatin B&W negative',
            defaultHalation: 12,
            defaultGrain: 48,
            defaultDirCouplers: 0,
          ),
        ],
        papers: [
          PrintPaperInfo(
            id: 'endura',
            name: 'Kodak Endura',
            subtitle: 'Warm RA-4 paper with smooth shadows',
          ),
          PrintPaperInfo(
            id: 'crystal_archive',
            name: 'Fuji Crystal Archive',
            subtitle: 'Crisp whites & cool clean contrast',
          ),
        ],
        presets: [
          LookPreset(
            id: 'builtin_portra_400',
            name: 'Kodak Portra 400',
            stockId: 'portra_400',
            paperId: 'endura',
            builtIn: true,
            contrast: 6,
            structure: 8,
            highlights: -14,
            shadows: 14,
            warmth: 10,
            dirCouplers: 55,
            halation: 18,
            grain: 24,
            fade: 6,
          ),
          LookPreset(
            id: 'builtin_cinestill_800t',
            name: 'CineStill 800T',
            stockId: 'cinestill_800t',
            paperId: 'crystal_archive',
            builtIn: true,
            contrast: 16,
            structure: 6,
            highlights: -18,
            shadows: 12,
            warmth: -16,
            tint: -6,
            dirCouplers: 52,
            halation: 72,
            grain: 42,
            fade: 10,
          ),
        ],
      );
}

class LookExportResult {
  const LookExportResult({
    required this.localPath,
    required this.elapsedMs,
    this.width = 0,
    this.height = 0,
  });

  final String localPath;
  final int elapsedMs;
  final int width;
  final int height;

  factory LookExportResult.fromMap(Map<dynamic, dynamic> map) {
    return LookExportResult(
      localPath: (map['localPath'] as String?) ?? '',
      elapsedMs: (map['elapsedMs'] as num?)?.toInt() ?? 0,
      width: (map['width'] as num?)?.toInt() ?? 0,
      height: (map['height'] as num?)?.toInt() ?? 0,
    );
  }
}

class RawEditorSession {
  const RawEditorSession({
    required this.rgbaBytes,
    required this.width,
    required this.height,
    required this.histRgb,
    required this.histW,
    required this.histH,
    required this.isLog2,
    required this.isTrueRaw,
  });

  final Uint8List rgbaBytes;
  final int width;
  final int height;
  final Uint8List histRgb;
  final int histW;
  final int histH;
  final bool isLog2;
  final bool isTrueRaw;

  static RawEditorSession? fromMap(Map<dynamic, dynamic> map) {
    final rgba = map['rgba'];
    final w = (map['width'] as num?)?.toInt() ?? 0;
    final h = (map['height'] as num?)?.toInt() ?? 0;
    if (rgba is! Uint8List || w <= 0 || h <= 0 || rgba.length < w * h * 4) {
      return null;
    }
    final histRaw = map['histRgb'];
    final histRgb = histRaw is Uint8List ? histRaw : Uint8List(0);
    return RawEditorSession(
      rgbaBytes: rgba,
      width: w,
      height: h,
      histRgb: histRgb,
      histW: (map['histW'] as num?)?.toInt() ?? 0,
      histH: (map['histH'] as num?)?.toInt() ?? 0,
      isLog2: (map['isLog2'] as bool?) ?? false,
      isTrueRaw: (map['isTrueRaw'] as bool?) ?? false,
    );
  }
}

class FilmStockShaderSpec {
  const FilmStockShaderSpec({
    required this.id,
    required this.isMono,
    required this.matrix,
    required this.gammaR,
    required this.gammaG,
    required this.gammaB,
    required this.toe,
    required this.shoulder,
    required this.dirOffDiag,
    required this.chromaGain,
    required this.grainSize,
  });

  final String id;
  final bool isMono;
  final List<double> matrix;
  final double gammaR;
  final double gammaG;
  final double gammaB;
  final double toe;
  final double shoulder;
  final List<double> dirOffDiag; // dm01, dm02, dm10, dm12, dm20, dm21
  final double chromaGain;
  final double grainSize;

  static const Map<String, FilmStockShaderSpec> byId = {
    'neutral': FilmStockShaderSpec(
      id: 'neutral',
      isMono: false,
      matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1],
      gammaR: 1.0,
      gammaG: 1.0,
      gammaB: 1.0,
      toe: 0.0,
      shoulder: 0.0,
      dirOffDiag: [0, 0, 0, 0, 0, 0],
      chromaGain: 1.0,
      grainSize: 0.0,
    ),
    'portra_400': FilmStockShaderSpec(
      id: 'portra_400',
      isMono: false,
      matrix: [0.96, 0.06, -0.02, -0.02, 0.99, 0.03, 0.01, 0.05, 0.94],
      gammaR: 0.94,
      gammaG: 0.96,
      gammaB: 0.99,
      toe: 0.14,
      shoulder: 0.36,
      dirOffDiag: [0.24, 0.10, 0.22, 0.18, 0.08, 0.20],
      chromaGain: 0.96,
      grainSize: 0.85,
    ),
    'portra_160': FilmStockShaderSpec(
      id: 'portra_160',
      isMono: false,
      matrix: [0.95, 0.06, -0.01, -0.01, 0.98, 0.03, 0.01, 0.04, 0.95],
      gammaR: 0.90,
      gammaG: 0.92,
      gammaB: 0.95,
      toe: 0.16,
      shoulder: 0.40,
      dirOffDiag: [0.20, 0.08, 0.18, 0.15, 0.06, 0.16],
      chromaGain: 0.91,
      grainSize: 0.58,
    ),
    'gold_200': FilmStockShaderSpec(
      id: 'gold_200',
      isMono: false,
      matrix: [1.03, 0.02, -0.05, -0.03, 0.97, 0.06, 0.02, 0.03, 0.95],
      gammaR: 1.05,
      gammaG: 1.02,
      gammaB: 0.98,
      toe: 0.12,
      shoulder: 0.28,
      dirOffDiag: [0.28, 0.14, 0.24, 0.20, 0.10, 0.22],
      chromaGain: 1.12,
      grainSize: 1.02,
    ),
    'ektar_100': FilmStockShaderSpec(
      id: 'ektar_100',
      isMono: false,
      matrix: [1.06, -0.03, -0.03, -0.04, 1.04, 0.00, 0.00, -0.04, 1.04],
      gammaR: 1.14,
      gammaG: 1.12,
      gammaB: 1.15,
      toe: 0.08,
      shoulder: 0.20,
      dirOffDiag: [0.34, 0.18, 0.30, 0.26, 0.16, 0.28],
      chromaGain: 1.24,
      grainSize: 0.50,
    ),
    'vision3_250d': FilmStockShaderSpec(
      id: 'vision3_250d',
      isMono: false,
      matrix: [0.97, 0.04, -0.01, -0.01, 0.97, 0.04, 0.02, 0.04, 0.94],
      gammaR: 0.88,
      gammaG: 0.90,
      gammaB: 0.92,
      toe: 0.20,
      shoulder: 0.44,
      dirOffDiag: [0.22, 0.10, 0.20, 0.18, 0.10, 0.18],
      chromaGain: 0.98,
      grainSize: 0.92,
    ),
    'cinestill_800t': FilmStockShaderSpec(
      id: 'cinestill_800t',
      isMono: false,
      matrix: [0.95, 0.02, 0.03, 0.01, 0.96, 0.03, 0.03, 0.06, 0.91],
      gammaR: 1.02,
      gammaG: 1.00,
      gammaB: 1.08,
      toe: 0.15,
      shoulder: 0.30,
      dirOffDiag: [0.22, 0.14, 0.18, 0.20, 0.12, 0.22],
      chromaGain: 1.04,
      grainSize: 1.25,
    ),
    'pro_400h': FilmStockShaderSpec(
      id: 'pro_400h',
      isMono: false,
      matrix: [0.92, 0.07, 0.01, -0.02, 1.01, 0.01, -0.01, 0.05, 0.96],
      gammaR: 0.91,
      gammaG: 0.95,
      gammaB: 0.94,
      toe: 0.18,
      shoulder: 0.38,
      dirOffDiag: [0.22, 0.10, 0.18, 0.16, 0.10, 0.18],
      chromaGain: 0.93,
      grainSize: 0.80,
    ),
    'velvia_50': FilmStockShaderSpec(
      id: 'velvia_50',
      isMono: false,
      matrix: [1.08, -0.05, -0.03, -0.04, 1.05, -0.01, -0.02, -0.03, 1.05],
      gammaR: 1.24,
      gammaG: 1.20,
      gammaB: 1.25,
      toe: 0.06,
      shoulder: 0.16,
      dirOffDiag: [0.32, 0.18, 0.28, 0.24, 0.18, 0.26],
      chromaGain: 1.28,
      grainSize: 0.45,
    ),
    'hp5_400': FilmStockShaderSpec(
      id: 'hp5_400',
      isMono: true,
      matrix: [0.27, 0.61, 0.12, 0.27, 0.61, 0.12, 0.27, 0.61, 0.12],
      gammaR: 1.08,
      gammaG: 1.08,
      gammaB: 1.08,
      toe: 0.14,
      shoulder: 0.30,
      dirOffDiag: [0, 0, 0, 0, 0, 0],
      chromaGain: 0.0,
      grainSize: 1.35,
    ),
  };

  static FilmStockShaderSpec of(String stockId) =>
      byId[stockId] ?? byId['portra_400']!;
}

class PrintPaperShaderSpec {
  const PrintPaperShaderSpec({
    required this.id,
    required this.contrast,
    required this.dMinR,
    required this.dMinG,
    required this.dMinB,
    required this.gainR,
    required this.gainG,
    required this.gainB,
  });

  final String id;
  final double contrast;
  final double dMinR;
  final double dMinG;
  final double dMinB;
  final double gainR;
  final double gainG;
  final double gainB;

  static const Map<String, PrintPaperShaderSpec> byId = {
    'endura': PrintPaperShaderSpec(
      id: 'endura',
      contrast: 1.06,
      dMinR: 0.012,
      dMinG: 0.010,
      dMinB: 0.006,
      gainR: 1.02,
      gainG: 1.00,
      gainB: 0.97,
    ),
    'crystal_archive': PrintPaperShaderSpec(
      id: 'crystal_archive',
      contrast: 1.10,
      dMinR: 0.006,
      dMinG: 0.010,
      dMinB: 0.013,
      gainR: 0.98,
      gainG: 1.01,
      gainB: 1.03,
    ),
    'neutral_scan': PrintPaperShaderSpec(
      id: 'neutral_scan',
      contrast: 1.00,
      dMinR: 0.0,
      dMinG: 0.0,
      dMinB: 0.0,
      gainR: 1.0,
      gainG: 1.0,
      gainB: 1.0,
    ),
    'warm_silver': PrintPaperShaderSpec(
      id: 'warm_silver',
      contrast: 1.12,
      dMinR: 0.014,
      dMinG: 0.011,
      dMinB: 0.004,
      gainR: 1.03,
      gainG: 1.00,
      gainB: 0.93,
    ),
  };

  static PrintPaperShaderSpec of(String paperId) =>
      byId[paperId] ?? byId['endura']!;
}
