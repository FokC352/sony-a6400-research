import 'package:flutter/material.dart';

import 'screens/home_screen.dart';
import 'services/sync_bridge.dart';

void main() {
  runApp(BionzLookLabApp(bridge: PlatformSyncBridge()));
}

class BionzLookLabApp extends StatelessWidget {
  const BionzLookLabApp({super.key, required this.bridge});

  final SyncBridge bridge;

  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFFFF6D00),
      brightness: Brightness.dark,
    );
    return MaterialApp(
      title: 'BIONZ Look Lab',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: const Color(0xFF0E1116),
        cardTheme: const CardThemeData(
          color: Color(0xFF171C24),
          elevation: 0,
        ),
      ),
      home: HomeScreen(bridge: bridge),
    );
  }
}
