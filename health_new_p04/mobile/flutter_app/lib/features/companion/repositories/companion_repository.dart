import '../../../core/network/api_client.dart';
import '../models/companion_status.dart';

class CompanionRepository {
  final ApiClient _client;

  CompanionRepository(this._client);

  Future<CompanionStatus> getStatus(String elderId) async {
    final response = await _client.get(
      'elders/${Uri.encodeComponent(elderId)}/robot-companion/status',
    );
    return CompanionStatus.fromJson(
      Map<String, dynamic>.from(response.data as Map),
    );
  }

  Future<CompanionStatus> start(String elderId) async {
    final response = await _client.post(
      'elders/${Uri.encodeComponent(elderId)}/robot-companion/start',
    );
    return CompanionStatus.fromJson(
      Map<String, dynamic>.from(response.data as Map),
    );
  }

  Future<CompanionStatus> stop(String elderId) async {
    final response = await _client.post(
      'elders/${Uri.encodeComponent(elderId)}/robot-companion/stop',
    );
    return CompanionStatus.fromJson(
      Map<String, dynamic>.from(response.data as Map),
    );
  }
}
