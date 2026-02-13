
def get_8d_steps_definitions():
    """Définition des 8 étapes du rapport 8D"""
    return [
        {'code': 'D1', 'name': 'Establish the Team'},
        {'code': 'D2', 'name': 'Describe the Problem'},
        {'code': 'D3', 'name': 'Develop Interim Containment Action'},
        {'code': 'D4', 'name': 'Determine Root Cause'},
        {'code': 'D5', 'name': 'Choose and Verify Permanent Corrective Actions'},
        {'code': 'D6', 'name': 'Implement Permanent Corrective Actions'},
        {'code': 'D7', 'name': 'Prevent Recurrence'},
        {'code': 'D8', 'name': 'Recognize Team and Individual Contributions'}
    ]

def generate_report_number():
    """Génère un numéro unique pour le rapport"""
    from datetime import datetime
    return f"8D-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

def validate_step_data(step_code: str, data: dict) -> bool:
    """Valide que les données requises sont présentes"""
    required_fields = {
        'D1': ['team_members', 'team_leader'],
        'D2': ['problem_description', 'impact'],
        'D3': ['containment_actions'],
        'D4': ['root_causes'],
        'D5': ['corrective_actions'],
        'D6': ['implementation_plan'],
        'D7': ['preventive_measures'],
        'D8': ['recognitions']
    }
    
    return all(field in data for field in required_fields.get(step_code, []))
