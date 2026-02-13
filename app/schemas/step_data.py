from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date
# ============================================================================
# D1 - Establish the Team
# ============================================================================
class TeamMember(BaseModel):
    """Membre de l'équipe 8D"""
    name: str = Field(..., description="Nom complet")
    function: str = Field(..., description="Fonction/Titre du poste")
    department: str = Field(..., description="Département")
    role: str = Field(..., description="Rôle dans l'équipe (production|maintenance|engineering|logistics|team_leader|other)")

class D1Data(BaseModel):
    """Structure de données pour l'étape D1 - Establish the Team"""
    team_members: List[TeamMember] = Field(
        default_factory=list,
        description="Liste des membres de l'équipe"
    )


# ============================================================================
# D2 - Describe the Problem
# ============================================================================
class FourW2H(BaseModel):
    """Analyse 4W2H du problème"""
    what: Optional[str] = Field(None, description="Quel défaut ?")
    where: Optional[str] = Field(None, description="Où ?")
    when: Optional[dt_date] = Field(None, description="Quand ?")
    who: Optional[str] = Field(None, description="Qui a détecté ?")
    how: Optional[str] = Field(None, description="Comment détecté ?")
    how_many: Optional[str] = Field(None, description="Quantité ? (avec unité)")

class IsIsNotFactor(BaseModel):
    """Facteur IS / IS NOT"""
    factor: str = Field(..., description="Nom du facteur (Product, Time, Lot, Pattern)")
    is_problem: Optional[str] = Field(None, description="Ce qui EST concerné")
    is_not_problem: Optional[str] = Field(None, description="Ce qui N'EST PAS concerné")

class D2Data(BaseModel):
    """Structure de données pour l'étape D2 - Describe the Problem"""
    # Section I - Description factuelle (4W2H)
    problem_description: Optional[str] = Field(
        None, 
        description="Description de l'objet/processus et du défaut"
    )
    four_w_2h: Optional[FourW2H] = None
    
    # Déviation vs Standard
    standard_applicable: Optional[str] = Field(None, description="Standard applicable")
    expected_situation: Optional[str] = Field(None, description="Situation attendue")
    observed_situation: Optional[str] = Field(None, description="Situation observée (déviation)")
    evidence_documents: Optional[str] = Field(None, description="Preuves et documents")
    
    # Section II - IS / IS NOT Analysis
    is_is_not_factors: List[IsIsNotFactor] = Field(
        default_factory=list,
        description="Analyse des facteurs IS / IS NOT"
    )
    
    # Impact & Preuves
    estimated_cost: Optional[float] = Field(None, description="Coût estimé")
    cost_currency: Optional[str] = Field("EUR", description="Devise (EUR|USD|CNY)")
    customer_impact: Optional[str] = Field(
        None, 
        description="Impact client (No|Low|Medium|High)"
    )
    additional_notes: Optional[str] = Field(None, description="Notes additionnelles")


# ============================================================================
# D3 - Develop Interim Containment Action
# ============================================================================
class DefectedPartStatus(BaseModel):
    """Statut des pièces défectueuses"""
    is_returned: bool = Field(False, description="Retourné ?")
    is_isolated: bool = Field(False, description="Isolé ?")
    isolation_location: Optional[str] = Field(None, description="Lieu d'isolation")
    is_identified: bool = Field(False, description="Identifié pour éviter mauvaise manipulation ?")
    identification_method: Optional[str] = Field(None, description="Méthode d'identification")

class SuspectedPartsLocation(BaseModel):
    """Statut des pièces suspectes par localisation"""
    location: str = Field(..., description="Localisation (Supplier site|In Transit|Production floor|Warehouse|Customer site|Others)")
    inventory: Optional[str] = Field(None, description="Inventaire/Quantité")
    actions: Optional[str] = Field(None, description="Actions prises")
    leader: Optional[str] = Field(None, description="Responsable")
    results: Optional[str] = Field(None, description="Résultats/Statut")

class AlertCommunication(BaseModel):
    """Communication des alertes"""
    production_shift_leaders: bool = Field(False)
    quality_control: bool = Field(False)
    warehouse: bool = Field(False)
    maintenance: bool = Field(False)
    customer_contact: bool = Field(False)
    production_planner: bool = Field(False)
    alert_reference: Optional[str] = Field(None, description="Alert # (QRQC log or NCR #)")

class RestartProduction(BaseModel):
    """Redémarrage de la production"""
    when: Optional[str] = Field(None, description="Quand (Date, Time, Lot)")
    first_certified_lot: Optional[str] = Field(None, description="Premier lot certifié")
    approved_by: Optional[str] = Field(None, description="Approuvé par")
    method: Optional[str] = Field(None, description="Méthode de vérification")
    identification_description: Optional[str] = Field(
        None, 
        description="Description identification pièces et cartons"
    )

class D3Data(BaseModel):
    """Structure de données pour l'étape D3 - Interim Containment"""
    # Section I - Defected Part Status
    defected_part_status: Optional[DefectedPartStatus] = None
    
    # Section II - Suspected Parts Status
    suspected_parts_locations: List[SuspectedPartsLocation] = Field(
        default_factory=list,
        description="Statut des pièces suspectes par localisation"
    )
    
    # Section III - Alert Communicated
    alert_communication: Optional[AlertCommunication] = None
    
    # Section IV - Restart Production
    restart_production: Optional[RestartProduction] = None
    
    # Section V - Containment Responsible
    containment_responsible: Optional[str] = Field(
        None, 
        description="Responsable du confinement"
    )


# ============================================================================
# D4 - Determine Root Cause
# ============================================================================
class FourMEnvironment(BaseModel):
    """4M + Environment pour analyse cause racine"""
    material: List[str] = Field(default_factory=list, description="Causes liées au Matériel")
    method: List[str] = Field(default_factory=list, description="Causes liées à la Méthode")
    machine: List[str] = Field(default_factory=list, description="Causes liées à la Machine")
    manpower: List[str] = Field(default_factory=list, description="Causes liées à la Main d'œuvre")
    environment: List[str] = Field(default_factory=list, description="Causes liées à l'Environnement")
    selected_problem: Optional[str] = Field(None, description="Cause racine sélectionnée")

class FiveWhyItem(BaseModel):
    """Un élément de l'analyse 5 Why"""
    question: Optional[str] = Field(None, description="Question Why")
    answer: Optional[str] = Field(None, description="Réponse")

class FiveWhys(BaseModel):
    """Analyse 5 Why"""
    why_1: Optional[FiveWhyItem] = None
    why_2: Optional[FiveWhyItem] = None
    why_3: Optional[FiveWhyItem] = None
    why_4: Optional[FiveWhyItem] = None
    why_5: Optional[FiveWhyItem] = None

class RootCauseConclusion(BaseModel):
    """Conclusion de la cause racine"""
    root_cause: Optional[str] = Field(None, description="Cause racine identifiée")
    validation_method: Optional[str] = Field(None, description="Comment a-t-elle été validée ?")

class D4Data(BaseModel):
    """Structure de données pour l'étape D4 - Root Cause Analysis"""
    # Section I - 4M + Environment OCCURRENCE
    four_m_occurrence: Optional[FourMEnvironment] = Field(
        None, 
        description="Analyse 4M+Environment pour l'occurrence"
    )
    
    # Section II - 5 Whys OCCURRENCE
    five_whys_occurrence: Optional[FiveWhys] = Field(
        None, 
        description="Analyse 5 Why pour l'occurrence"
    )
    
    # Section III - Root Cause for Occurrence
    root_cause_occurrence: Optional[RootCauseConclusion] = None
    
    # Section IV - 4M + Environment NON-DETECTION
    four_m_non_detection: Optional[FourMEnvironment] = Field(
        None, 
        description="Analyse 4M+Environment pour la non-détection"
    )
    
    # Section V - 5 Whys NON-DETECTION
    five_whys_non_detection: Optional[FiveWhys] = Field(
        None, 
        description="Analyse 5 Why pour la non-détection"
    )
    
    # Section VI - Root Cause for NON-DETECTION
    root_cause_non_detection: Optional[RootCauseConclusion] = None


# ============================================================================
# D5 - Choose and Verify Permanent Corrective Actions
# ============================================================================
class CorrectiveAction(BaseModel):
    """Action corrective (occurrence ou détection)"""
    action: str = Field(..., description="Description de l'action")
    responsible: str = Field(..., description="Responsable")
    due_date: Optional[date] = Field(None, description="Date d'échéance")
    implementation_date: Optional[date] = Field(None, alias="imp_date", description="Date de mise en œuvre")
    evidence: Optional[str] = Field(None, description="Preuve/référence")

class D5Data(BaseModel):
    """Structure de données pour l'étape D5 - Corrective Actions"""
    # Section I - Corrective Action for Occurrence
    corrective_actions_occurrence: List[CorrectiveAction] = Field(
        default_factory=list,
        description="Actions correctives pour l'occurrence"
    )
    
    # Section II - Corrective Action for Detection
    corrective_actions_detection: List[CorrectiveAction] = Field(
        default_factory=list,
        description="Actions correctives pour la détection"
    )


# ============================================================================
# D6 - Implement Permanent Corrective Actions
# ============================================================================
class ImplementationMonitoring(BaseModel):
    """Suivi de la mise en œuvre"""
    monitoring_interval: Optional[str] = Field(None, description="Intervalle de surveillance")
    pieces_produced: Optional[int] = Field(None, description="Nombre de pièces produites")
    rejection_rate: Optional[float] = Field(None, description="Taux de rejet (%)")
    audited_by: Optional[str] = Field(None, description="Audité par")
    audit_date: Optional[date] = Field(None, description="Date d'audit")
    shift_1_data: Optional[str] = Field(None, description="Données Shift 1")
    shift_2_data: Optional[str] = Field(None, description="Données Shift 2")

class ImplementationChecklistItem(BaseModel):
    """Item de la checklist d'implémentation"""
    question: str = Field(..., description="Question de vérification")
    checked: bool = Field(False, description="Vérifié ?")

class D6Data(BaseModel):
    """Structure de données pour l'étape D6 - Implementation"""
    # Section II - Implementation & Effectiveness Check - Monitoring
    monitoring: Optional[ImplementationMonitoring] = None
    
    # Section III - Implementation Checklist
    checklist: List[ImplementationChecklistItem] = Field(
        default_factory=list,
        description="Checklist d'implémentation"
    )


# ============================================================================
# D7 - Prevent Recurrence
# ============================================================================
class RecurrenceRisk(BaseModel):
    """Risque de récurrence ailleurs"""
    area_line_product: Optional[str] = Field(None, description="Zone/Ligne/Produit")
    similar_risk_present: Optional[str] = Field(
        None, 
        description="Risque similaire présent ? (yes|no|unknown)"
    )
    action_taken: Optional[str] = Field(None, description="Action prise")

from datetime import date as dt_date

class LessonLearningDissemination(BaseModel):
    audience_team: Optional[str] = Field(None, description="Audience/Équipe")
    method: Optional[str] = Field(None, description="Méthode (Meeting, LLC, Email)")
    date: Optional[dt_date] = Field(None, description="Date")
    owner: Optional[str] = Field(None, description="Responsable")
    evidence: Optional[str] = Field(None, description="Preuve")


class ReplicationValidation(BaseModel):
    """Validation de la réplication"""
    line_site: Optional[str] = Field(None, description="Ligne/Site")
    action_replicated: Optional[str] = Field(None, description="Action répliquée")
    confirmation_method: Optional[str] = Field(None, description="Méthode de confirmation")
    confirmed_by: Optional[str] = Field(None, description="Confirmé par")

class KnowledgeBaseUpdate(BaseModel):
    """Mise à jour de la base de connaissances"""
    document_type: Optional[str] = Field(None, description="Type de document")
    topic_reference: Optional[str] = Field(None, description="Sujet/Référence")
    owner: Optional[str] = Field(None, description="Responsable")
    location_link: Optional[str] = Field(None, description="Localisation/Lien")

class LongTermMonitoring(BaseModel):
    """Surveillance à long terme"""
    checkpoint_type: Optional[str] = Field(None, description="Type de point de contrôle")
    frequency: Optional[str] = Field(None, description="Fréquence")
    owner: Optional[str] = Field(None, description="Responsable")
    start_date: Optional[date] = Field(None, description="Date de début")
    notes: Optional[str] = Field(None, description="Notes")

class D7Data(BaseModel):
    """Structure de données pour l'étape D7 - Prevention & Replication"""
    # Section I - Risk of Recurrence Elsewhere
    recurrence_risks: List[RecurrenceRisk] = Field(
        default_factory=list,
        description="Risques de récurrence ailleurs"
    )
    
    # Section II - Lesson Learning Dissemination
    lesson_disseminations: List[LessonLearningDissemination] = Field(
        default_factory=list,
        description="Diffusions des leçons apprises"
    )
    
    # Section III - Replication Validation
    replication_validations: List[ReplicationValidation] = Field(
        default_factory=list,
        description="Validations de réplication"
    )
    
    # Section IV - Knowledge Base Update
    knowledge_base_updates: List[KnowledgeBaseUpdate] = Field(
        default_factory=list,
        description="Mises à jour de la base de connaissances"
    )
    
    # Section V - Long-Term Monitoring
    long_term_monitoring: List[LongTermMonitoring] = Field(
        default_factory=list,
        description="Surveillance à long terme"
    )
    
    # Section VI - LL Conclusion
    ll_conclusion: Optional[str] = Field(
        None, 
        description="Conclusion des leçons apprises"
    )


# ============================================================================
# D8 - Recognize Team and Individual Contributions
# ============================================================================
class ClosureSignature(BaseModel):
    """Signatures de clôture"""
    closed_by: Optional[str] = Field(None, description="Fermé par (nom et titre)")
    closure_date: Optional[date] = Field(None, description="Date de clôture")
    approved_by: Optional[str] = Field(None, description="Approuvé par (Quality Manager)")
    approval_date: Optional[date] = Field(None, description="Date d'approbation")

class D8Data(BaseModel):
    """Structure de données pour l'étape D8 - Closure & Capitalization"""
    # Final Closure Statement
    closure_statement: Optional[str] = Field(
        None, 
        description="Déclaration finale de clôture (satisfaction client, non-récurrence, apprentissage, documentation)"
    )
    
    # Signatures & Validation
    signatures: Optional[ClosureSignature] = None


# ============================================================================
# Map des schémas par step_code
# ============================================================================
STEP_SCHEMAS = {
    'D1': D1Data,
    'D2': D2Data,
    'D3': D3Data,
    'D4': D4Data,
    'D5': D5Data,
    'D6': D6Data,
    'D7': D7Data,
    'D8': D8Data,
}


def get_step_schema(step_code: str):
    """Récupère le schéma Pydantic pour un step_code donné"""
    return STEP_SCHEMAS.get(step_code)