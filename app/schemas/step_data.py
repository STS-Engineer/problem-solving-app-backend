from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date as dt_date
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
class  DefectedPartStatus(BaseModel):
    """Status of defected parts"""
    returned: bool = Field(False, description="Returned?")
    isolated: bool = Field(False, description="Isolated?")
    isolated_location: Optional[str] = Field(None, description="Isolation location")
    identified: bool = Field(False, description="Identified to avoid mishandling?")
    identified_method: Optional[str] = Field(None, description="Identification method")

class SuspectedPartsRow(BaseModel):
    """Suspected parts by location"""
    location: str = Field(..., description="Location (supplier_site|in_transit|production_floor|warehouse|customer_site|others)")
    inventory: Optional[str] = Field(None, description="Inventory/Quantity")
    actions: Optional[str] = Field(None, description="Actions taken")
    leader: Optional[str] = Field(None, description="Leader")
    results: Optional[str] = Field(None, description="Results/Status")

class AlertCommunicatedTo(BaseModel):
    """Alert communication"""
    production_shift_leaders: bool = Field(False)
    quality_control: bool = Field(False)
    warehouse: bool = Field(False)
    maintenance: bool = Field(False)
    customer_contact: bool = Field(False)
    production_planner: bool = Field(False)

class RestartProduction(BaseModel):
    """Production restart"""
    when: Optional[str] = Field(None, description="When (Date, Time, Lot)")
    first_certified_lot: Optional[str] = Field(None, description="First certified lot")
    approved_by: Optional[str] = Field(None, description="Approved by")
    method: Optional[str] = Field(None, description="Verification method")
    identification: Optional[str] = Field(None, description="Parts and boxes identification")

class D3Data(BaseModel):
    """D3 - Interim Containment data structure"""
    defected_part_status: DefectedPartStatus
    suspected_parts_status: List[SuspectedPartsRow] = Field(
        default_factory=list,
        description="Suspected parts status by location"
    )
    alert_communicated_to: AlertCommunicatedTo
    alert_number: Optional[str] = Field(None, description="Alert # (QRQC log or NCR #)")
    restart_production: RestartProduction
    containment_responsible: Optional[str] = Field(
        None, 
        description="Containment responsible"
    )


# ============================================================================
# D4 - Determine Root Cause
# ============================================================================
class FourMRow(BaseModel):
    """Single row in 4M table (A1, E1, C1, N1, S1)"""
    material: Optional[str] = Field(None, description="Material cause")
    method: Optional[str] = Field(None, description="Method cause")
    machine: Optional[str] = Field(None, description="Machine cause")
    manpower: Optional[str] = Field(None, description="Manpower cause")
    environment: Optional[str] = Field(None, description="Environment cause")

class FourMEnvironment(BaseModel):
    """4M + Environment analysis with 3 rows + selected problem"""
    row_1: Optional[FourMRow] = None
    row_2: Optional[FourMRow] = None
    row_3: Optional[FourMRow] = None
    selected_problem: Optional[str] = Field(None, description="Selected root cause")

class FiveWhyItem(BaseModel):
    """Single Why analysis item"""
    question: Optional[str] = Field(None, description="Why question")
    answer: Optional[str] = Field(None, description="Answer")

class FiveWhys(BaseModel):
    """5 Why analysis"""
    why_1: Optional[FiveWhyItem] = None
    why_2: Optional[FiveWhyItem] = None
    why_3: Optional[FiveWhyItem] = None
    why_4: Optional[FiveWhyItem] = None
    why_5: Optional[FiveWhyItem] = None

class RootCauseConclusion(BaseModel):
    """Root cause conclusion"""
    root_cause: Optional[str] = Field(None, description="Identified root cause")
    validation_method: Optional[str] = Field(None, description="How was it validated?")

class D4Data(BaseModel):
    """D4 - Root Cause Analysis data structure"""
    four_m_occurrence: Optional[FourMEnvironment] = None
    five_whys_occurrence: Optional[FiveWhys] = None
    root_cause_occurrence: Optional[RootCauseConclusion] = None
    four_m_non_detection: Optional[FourMEnvironment] = None
    five_whys_non_detection: Optional[FiveWhys] = None
    root_cause_non_detection: Optional[RootCauseConclusion] = None


# ============================================================================
# D5 - Choose and Verify Permanent Corrective Actions
# ============================================================================
class CorrectiveAction(BaseModel):
    """Corrective action item"""
    action: Optional[str] = Field(None, description="Action description")
    responsible: Optional[str] = Field(None, description="Responsible person")
    due_date: Optional[str] = Field(None, description="Due date")
    imp_date: Optional[str] = Field(None, description="Implementation date")
    evidence: Optional[str] = Field(None, description="Evidence reference")

class D5Data(BaseModel):
    """D5 - Corrective Actions data structure"""
    corrective_actions_occurrence: List[CorrectiveAction] = Field(
        default_factory=list,
        description="Corrective actions for occurrence"
    )
    corrective_actions_detection: List[CorrectiveAction] = Field(
        default_factory=list,
        description="Corrective actions for detection"
    )


# ============================================================================
# D6 - Implement Permanent Corrective Actions
# ============================================================================
class ImplementationMonitoring(BaseModel):
    """Implementation monitoring"""
    monitoring_interval: Optional[str] = Field(None, description="Monitoring interval of time")
    pieces_produced: Optional[int] = Field(None, description="Number of pieces produced")
    rejection_rate: Optional[float] = Field(None, description="Rejection rate (%)")
    audited_by: Optional[str] = Field(None, description="Audited by (name and title)")
    audit_date: Optional[str] = Field(None, description="Audit date")
    shift_1_data: Optional[str] = Field(None, description="Shift 1 data")
    shift_2_data: Optional[str] = Field(None, description="Shift 2 data")

class ImplementationChecklistItem(BaseModel):
    """Implementation checklist item"""
    question: str = Field(..., description="Verification question")
    checked: bool = Field(False, description="Checked?")
    shift_1: bool = Field(False, description="Shift 1 verification")
    shift_2: bool = Field(False, description="Shift 2 verification")
    shift_3: bool = Field(False, description="Shift 3 verification")

class D6Data(BaseModel):
    """D6 - Implementation & Effectiveness Check data structure"""
    monitoring: Optional[ImplementationMonitoring] = Field(
        None,
        description="Section II - Implementation monitoring data"
    )
    checklist: List[ImplementationChecklistItem] = Field(
        default_factory=list,
        description="Section III - Implementation verification checklist"
    )


# ============================================================================
# D7 - Prevent Recurrence
# ============================================================================
class RecurrenceRisk(BaseModel):
    """Risk of recurrence elsewhere"""
    area_line_product: Optional[str] = Field(None, description="Area/Line/Product")
    similar_risk_present: Optional[str] = Field(None, description="yes|no|unknown")
    action_taken: Optional[str] = Field(None, description="Action taken")

class LessonLearningDissemination(BaseModel):
    """Lesson learning dissemination"""
    audience_team: Optional[str] = Field(None, description="Audience/Team")
    method: Optional[str] = Field(None, description="Method (Meeting, LLC, Email)")
    date: Optional[str] = Field(None, description="Date")
    owner: Optional[str] = Field(None, description="Owner")
    evidence: Optional[str] = Field(None, description="Evidence")

class ReplicationValidation(BaseModel):
    """Replication validation"""
    line_site: Optional[str] = Field(None, description="Line/Site")
    action_replicated: Optional[str] = Field(None, description="Action replicated")
    confirmation_method: Optional[str] = Field(None, description="Confirmation method")
    confirmed_by: Optional[str] = Field(None, description="Confirmed by")

class KnowledgeBaseUpdate(BaseModel):
    """Knowledge base update"""
    document_type: Optional[str] = Field(None, description="Document type")
    topic_reference: Optional[str] = Field(None, description="Topic/Reference")
    owner: Optional[str] = Field(None, description="Owner")
    location_link: Optional[str] = Field(None, description="Location/Link")

class LongTermMonitoring(BaseModel):
    """Long-term monitoring"""
    checkpoint_type: Optional[str] = Field(None, description="Checkpoint type")
    frequency: Optional[str] = Field(None, description="Frequency")
    owner: Optional[str] = Field(None, description="Owner")
    start_date: Optional[str] = Field(None, description="Start date")
    notes: Optional[str] = Field(None, description="Notes")

class D7Data(BaseModel):
    """D7 - Prevention & Replication data structure"""
    recurrence_risks: List[RecurrenceRisk] = Field(default_factory=list)
    lesson_disseminations: List[LessonLearningDissemination] = Field(default_factory=list)
    replication_validations: List[ReplicationValidation] = Field(default_factory=list)
    knowledge_base_updates: List[KnowledgeBaseUpdate] = Field(default_factory=list)
    long_term_monitoring: List[LongTermMonitoring] = Field(default_factory=list)
    ll_conclusion: Optional[str] = Field(None, description="Lessons learned conclusion")


# ============================================================================
# D8 - Recognize Team and Individual Contributions
# ============================================================================
class ClosureSignature(BaseModel):
    """Closure signatures"""
    closed_by: Optional[str] = Field(None, description="Closed by (name and title)")
    closure_date: Optional[str] = Field(None, description="Closure date")
    approved_by: Optional[str] = Field(None, description="Approved by (Quality Manager)")
    approval_date: Optional[str] = Field(None, description="Approval date")

class D8Data(BaseModel):
    """D8 - Closure & Capitalization data structure"""
    closure_statement: Optional[str] = Field(
        None, 
        description="Final closure statement"
    )
    signatures: Optional[ClosureSignature] = None