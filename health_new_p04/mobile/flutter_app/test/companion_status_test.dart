import 'package:flutter_test/flutter_test.dart';
import 'package:ai_health_iot_flutter/features/companion/models/companion_status.dart';

void main() {
  test('parses wireless UWB companion status returned by health_new', () {
    final status = CompanionStatus.fromJson({
      'elder_id': 'elder01_02',
      'elder_name': '李四',
      'state': 'FOLLOWING',
      'reason': 'running',
      'runtime_active': true,
      'can_start': false,
      'can_stop': true,
      'robot': {'online': true},
      'uwb': {'valid': true, 'distance_m': 1.36, 'bearing_deg': -12.4},
    });

    expect(status.state, 'FOLLOWING');
    expect(status.runtimeActive, isTrue);
    expect(status.robotOnline, isTrue);
    expect(status.uwbValid, isTrue);
    expect(status.distanceM, 1.36);
    expect(status.bearingDeg, -12.4);
  });
}
