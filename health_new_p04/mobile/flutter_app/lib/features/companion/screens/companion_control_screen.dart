import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../../core/theme/app_colors.dart';
import '../models/companion_status.dart';
import '../repositories/companion_repository.dart';

class CompanionElderOption {
  final String id;
  final String name;

  const CompanionElderOption({required this.id, required this.name});
}

class CompanionControlScreen extends StatefulWidget {
  final CompanionRepository repository;
  final List<CompanionElderOption> elders;

  const CompanionControlScreen({
    super.key,
    required this.repository,
    required this.elders,
  });

  @override
  State<CompanionControlScreen> createState() => _CompanionControlScreenState();
}

class _CompanionControlScreenState extends State<CompanionControlScreen> {
  CompanionStatus? _status;
  Timer? _timer;
  late String _elderId;
  bool _loading = false;
  bool _mutating = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _elderId = widget.elders.first.id;
    _refresh();
    _timer = Timer.periodic(
      const Duration(milliseconds: 1500),
      (_) => _refresh(quiet: true),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh({bool quiet = false}) async {
    if (_loading || _mutating) return;
    _loading = true;
    if (!quiet && mounted) setState(() {});
    try {
      final status = await widget.repository.getStatus(_elderId);
      if (mounted) {
        setState(() {
          _status = status;
          _error = null;
        });
      }
    } catch (error) {
      if (!quiet && mounted) setState(() => _error = _message(error));
    } finally {
      _loading = false;
      if (!quiet && mounted) setState(() {});
    }
  }

  Future<void> _start() async {
    await _mutate(() => widget.repository.start(_elderId));
  }

  Future<void> _stop() async {
    await _mutate(() => widget.repository.stop(_elderId));
  }

  Future<void> _mutate(Future<CompanionStatus> Function() operation) async {
    if (_mutating) return;
    setState(() {
      _mutating = true;
      _error = null;
    });
    try {
      final status = await operation();
      if (mounted) setState(() => _status = status);
    } catch (error) {
      if (mounted) setState(() => _error = _message(error));
    } finally {
      if (mounted) setState(() => _mutating = false);
    }
  }

  String _message(Object error) {
    if (error is DioException) {
      final detail = error.response?.data is Map
          ? (error.response?.data as Map)['detail']
          : null;
      if (detail is Map && detail['message'] is String) {
        return detail['message'] as String;
      }
      if (detail is String) return detail;
    }
    return '伴随控制请求失败，请检查 Go2 Runtime、UWB 和网络。';
  }

  @override
  Widget build(BuildContext context) {
    final status = _status;
    return Scaffold(
      appBar: AppBar(title: const Text('Go2 UWB 伴随')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          DropdownButtonFormField<String>(
            initialValue: _elderId,
            decoration: const InputDecoration(labelText: '监护对象'),
            items: widget.elders
                .map(
                  (elder) => DropdownMenuItem(
                    value: elder.id,
                    child: Text(elder.name),
                  ),
                )
                .toList(growable: false),
            onChanged: _mutating
                ? null
                : (value) {
                    if (value == null || value == _elderId) return;
                    setState(() {
                      _elderId = value;
                      _status = null;
                      _error = null;
                    });
                    _refresh();
                  },
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    status?.state ?? (_loading ? '正在读取…' : '状态不可用'),
                    style: const TextStyle(
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 12),
                  _row('Go2', status?.robotOnline == true ? '在线' : '离线'),
                  _row('UWB', status?.uwbValid == true ? '有效' : '不可用'),
                  _row(
                    '距离',
                    status?.distanceM == null
                        ? '--'
                        : '${status!.distanceM!.toStringAsFixed(2)} m',
                  ),
                  _row(
                    '方向',
                    status?.bearingDeg == null
                        ? '--'
                        : '${status!.bearingDeg!.toStringAsFixed(1)}°',
                  ),
                ],
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: const TextStyle(color: AppColors.error)),
          ],
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: ElevatedButton.icon(
                  onPressed:
                      status?.canStart == true && !_mutating ? _start : null,
                  icon: const Icon(Icons.play_arrow),
                  label: Text(_mutating ? '处理中…' : '开始伴随'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed:
                      status?.canStop == true && !_mutating ? _stop : null,
                  icon: const Icon(Icons.stop),
                  label: const Text('停止伴随'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          const Text(
            '按钮只调用 health_new 高层生命周期；UWB 无效、风险锁或运动控制忙时不会启动。',
            style: TextStyle(color: AppColors.textSub),
          ),
        ],
      ),
    );
  }

  Widget _row(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }
}
