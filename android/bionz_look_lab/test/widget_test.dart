import 'dart:async';
import 'dart:typed_data';

import 'package:bionz_look_lab/main.dart';
import 'package:bionz_look_lab/models/look_models.dart';
import 'package:bionz_look_lab/models/sync_models.dart';
import 'package:bionz_look_lab/services/sync_bridge.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeSyncBridge implements SyncBridge {
  final StreamController<SyncSnapshot> _ctrl =
      StreamController<SyncSnapshot>.broadcast();
  SyncConfig _config = const SyncConfig();
  LookCatalog _catalog = LookCatalog.fallbackDefaults();
  bool started = false;

  @override
  Stream<SyncSnapshot> get events => _ctrl.stream;

  @override
  Future<void> clearLogs() async {}

  @override
  Future<SyncConfig> getConfig() async => _config;

  @override
  Future<SyncSnapshot> getSnapshot() async => const SyncSnapshot(
        serviceRunning: false,
        connectionState: 'Connected',
        chanspec: 'Ch 36 (80 MHz)',
        syncedCount: 2,
        recentFiles: [
          SyncedFile(
            cameraId: 'a6400000',
            dcfName: 'DSC00123.ARW',
            seqId: 12,
            sizeBytes: 25165824,
            crc32Hex: 'deadbeef',
            localPath: 'DCIM/BionzLookLab/DSC00123.ARW',
            syncedAtMs: 1700000000,
          ),
        ],
      );

  @override
  Future<bool> isIgnoringBatteryOptimizations() async => true;

  @override
  Future<void> openWifiSettings() async {}

  @override
  Future<void> requestBatteryOptimizationExemption() async {}

  @override
  Future<SyncConfig> saveConfig(SyncConfig config) async {
    _config = config;
    return config;
  }

  @override
  Future<void> startService() async {
    started = true;
  }

  @override
  Future<void> stopService() async {
    started = false;
  }

  @override
  Future<void> syncNow() async {}

  @override
  Future<LookCatalog> getLookCatalog() async => _catalog;

  @override
  Future<LookCatalog> saveLookPreset(LookPreset preset) async {
    final next = [
      ..._catalog.presets.where((p) => p.id != preset.id),
      preset,
    ];
    _catalog = LookCatalog(
      stocks: _catalog.stocks,
      papers: _catalog.papers,
      presets: next,
      autoApplyPresetId: _catalog.autoApplyPresetId,
    );
    return _catalog;
  }

  @override
  Future<LookCatalog> deleteLookPreset(String id) async {
    final next = _catalog.presets.where((p) => p.id != id).toList();
    _catalog = LookCatalog(
      stocks: _catalog.stocks,
      papers: _catalog.papers,
      presets: next,
      autoApplyPresetId:
          _catalog.autoApplyPresetId == id ? null : _catalog.autoApplyPresetId,
    );
    return _catalog;
  }

  @override
  Future<LookCatalog> setAutoApplyPreset(String? id) async {
    _catalog = LookCatalog(
      stocks: _catalog.stocks,
      papers: _catalog.papers,
      presets: _catalog.presets,
      autoApplyPresetId: id,
    );
    return _catalog;
  }

  @override
  Future<Uint8List?> loadPhotoThumbnail(String dcfName) async => null;

  @override
  Future<Uint8List?> renderPhotoPreview(
    String dcfName,
    LookPreset preset,
  ) async =>
      null;

  @override
  Future<PreviewRenderResult?> renderPhotoPreviewWithHistogram(
    String dcfName,
    LookPreset preset,
  ) async =>
      null;

  @override
  Future<RawEditorSession?> loadRawEditorSession(String dcfName) async => null;

  @override
  Future<Set<String>> getGradedPhotos() async => const {};

  @override
  Future<LookExportResult> exportGradedPhoto(
    String dcfName,
    LookPreset preset,
  ) async =>
      LookExportResult(
        localPath: 'DCIM/BionzLookLab/Graded/${dcfName}_test.JPG',
        elapsedMs: 120,
      );
}

void main() {
  test('SyncSnapshot parses camera status and files safely', () {
    final snap = SyncSnapshot.fromMap({
      'serviceRunning': true,
      'connectionState': 'Connected',
      'ssid': 'A6400-5G-SYNC',
      'cameraId': 'a6400001',
      'powerState': 'ACTIVE',
      'chanspec': 'Ch 36 (80 MHz)',
      'syncedCount': 1,
      'recentFiles': [
        {
          'cameraId': 'a6400001',
          'dcfName': 'DSC00005.ARW',
          'seqId': 5,
          'sizeBytes': 24000000,
          'crc32': '01020304',
          'localPath': 'DCIM/BionzLookLab/DSC00005.ARW',
          'syncedAtMs': 42,
        },
      ],
    });
    expect(snap.serviceRunning, isTrue);
    expect(snap.recentFiles, hasLength(1));
    expect(snap.recentFiles.first.isRaw, isTrue);
    expect(snap.recentFiles.first.crc32Hex, '01020304');
  });

  test('LookPreset clamps RAW Develop & film parameters and round-trips maps', () {
    const base = LookPreset(id: 'p1', name: 'Portra');
    final tuned = base
        .withParam(AdjustParam.structure, 150)
        .withParam(AdjustParam.grain, -10)
        .withParam(AdjustParam.halation, 72)
        .withParam(AdjustParam.whites, -25)
        .withParam(AdjustParam.blacks, 18)
        .withParam(AdjustParam.vibrance, 40)
        .withParam(AdjustParam.clarity, 32)
        .withParam(AdjustParam.sharpen, 45);
    expect(tuned.structure, 100);
    expect(tuned.grain, 0);
    expect(tuned.halation, 72);
    expect(tuned.whites, -25);
    expect(tuned.blacks, 18);
    expect(tuned.vibrance, 40);
    expect(tuned.clarity, 32);
    expect(tuned.sharpen, 45);

    final decoded = LookPreset.fromMap(tuned.toMap());
    expect(decoded.id, 'p1');
    expect(decoded.structure, 100);
    expect(decoded.halation, 72);
    expect(decoded.whites, -25);
    expect(decoded.clarity, 32);
    expect(decoded.sharpen, 45);
  });

  test('SyncedFile detects interim preview entries', () {
    const prev = SyncedFile(
      cameraId: 'a6c5b626',
      dcfName: '_DSC1792.ARW',
      seqId: 1,
      sizeBytes: 350000,
      crc32Hex: 'preview',
      localPath: 'DCIM/BionzLookLab/_DSC1792_preview.JPG',
      syncedAtMs: 1,
    );
    expect(prev.isRaw, isTrue);
    expect(prev.isPreview, isTrue);
    const full = SyncedFile(
      cameraId: 'a6c5b626',
      dcfName: '_DSC1792.ARW',
      seqId: 1,
      sizeBytes: 24850000,
      crc32Hex: 'abcd1234',
      localPath: 'DCIM/BionzLookLab/_DSC1792.ARW',
      syncedAtMs: 2,
    );
    expect(full.isPreview, isFalse);
  });

  test('SyncConfig serializes arwInstantPreview and copies cleanly', () {
    const def = SyncConfig();
    expect(def.arwInstantPreview, isFalse);
    final updated = def.copyWith(arwInstantPreview: true);
    expect(updated.arwInstantPreview, isTrue);
    final roundTrip = SyncConfig.fromMap(updated.toMap());
    expect(roundTrip.arwInstantPreview, isTrue);
    expect(roundTrip.ssid, def.ssid);
  });

  testWidgets(
      'App shows unified single-screen UI, sheets, and GPU/fallback Look Lab editor',
      (WidgetTester tester) async {
    final bridge = _FakeSyncBridge();
    await tester.pumpWidget(BionzLookLabApp(bridge: bridge));
    await tester.pumpAndSettle();

    expect(find.text('Live Sync'), findsOneWidget);
    expect(find.text('Auto-Apply Film Preset'), findsOneWidget);
    expect(find.text('OFF (Raw only)'), findsOneWidget);
    expect(find.text('Kodak Portra 400'), findsOneWidget);
    expect(find.text('Synced Photos (2)'), findsOneWidget);
    expect(find.text('DSC00123'), findsOneWidget);
    expect(find.text('RAW'), findsOneWidget);

    await tester.tap(find.byTooltip('Sync Details & Logs'));
    await tester.pumpAndSettle();
    expect(find.text('Live Sync Details & Logs'), findsOneWidget);
    expect(find.text('Start Background Sync'), findsOneWidget);
    await tester.tap(find.byTooltip('Close'));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Camera & Look Lab Settings'), findsOneWidget);
    expect(find.text('ARW Instant Preview + Background RAW'), findsOneWidget);
    await tester.tap(find.text('ARW Instant Preview + Background RAW'));
    await tester.pumpAndSettle();
    expect((await bridge.getConfig()).arwInstantPreview, isTrue);
    await tester.tap(find.byTooltip('Close'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Kodak Portra 400'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('DSC00123'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('DSC00123'));
    await tester.pumpAndSettle();

    expect(find.text('Presets'), findsOneWidget);
    expect(find.text('Film'), findsOneWidget);
    expect(find.text('Light'), findsOneWidget);
    expect(find.text('Color'), findsOneWidget);
    expect(find.text('Detail'), findsOneWidget);
    expect(find.text('Export'), findsOneWidget);

    await tester.tap(find.text('Detail'));
    await tester.pumpAndSettle();
    expect(find.text('Structure +8'), findsWidgets);

    await tester.tap(find.byTooltip('Cancel'));
    await tester.pumpAndSettle();
    expect(find.text('Synced Photos (2)'), findsOneWidget);
  });
}
