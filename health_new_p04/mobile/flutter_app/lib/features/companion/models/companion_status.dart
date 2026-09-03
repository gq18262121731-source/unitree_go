class CompanionStatus {
  final String elderId;
  final String elderName;
  final String state;
  final String reason;
  final bool runtimeActive;
  final bool canStart;
  final bool canStop;
  final bool robotOnline;
  final bool uwbValid;
  final double? distanceM;
  final double? bearingDeg;

  const CompanionStatus({
    required this.elderId,
    required this.elderName,
    required this.state,
    required this.reason,
    required this.runtimeActive,
    required this.canStart,
    required this.canStop,
    required this.robotOnline,
    required this.uwbValid,
    required this.distanceM,
    required this.bearingDeg,
  });

  factory CompanionStatus.fromJson(Map<String, dynamic> json) {
    final robot = json['robot'] as Map<String, dynamic>? ?? const {};
    final uwb = json['uwb'] as Map<String, dynamic>? ?? const {};
    return CompanionStatus(
      elderId: json['elder_id'] as String? ?? '',
      elderName: json['elder_name'] as String? ?? '未命名老人',
      state: json['state'] as String? ?? 'IDLE',
      reason: json['reason'] as String? ?? '',
      runtimeActive: json['runtime_active'] == true,
      canStart: json['can_start'] == true,
      canStop: json['can_stop'] == true,
      robotOnline: robot['online'] == true,
      uwbValid: uwb['valid'] == true,
      distanceM: _number(uwb['distance_m']),
      bearingDeg: _number(uwb['bearing_deg']),
    );
  }

  static double? _number(Object? value) {
    return value is num ? value.toDouble() : null;
  }
}
