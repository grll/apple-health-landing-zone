from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass


# Enums for known types
class BiologicalSex(str, Enum):
    MALE = "HKBiologicalSexMale"
    FEMALE = "HKBiologicalSexFemale"
    OTHER = "HKBiologicalSexOther"
    NOT_SET = "HKBiologicalSexNotSet"


class BloodType(str, Enum):
    A_POSITIVE = "HKBloodTypeAPositive"
    A_NEGATIVE = "HKBloodTypeANegative"
    B_POSITIVE = "HKBloodTypeBPositive"
    B_NEGATIVE = "HKBloodTypeBNegative"
    AB_POSITIVE = "HKBloodTypeABPositive"
    AB_NEGATIVE = "HKBloodTypeABNegative"
    O_POSITIVE = "HKBloodTypeOPositive"
    O_NEGATIVE = "HKBloodTypeONegative"
    NOT_SET = "HKBloodTypeNotSet"


class EyeSide(str, Enum):
    LEFT = "left"
    RIGHT = "right"


# Base Models
class TimestampedBase(SQLModel):
    """Base model for entities with date tracking"""

    start_date: datetime = Field(index=True)  # Indexed for date range queries
    end_date: datetime = Field(index=True)
    creation_date: datetime | None = None


class SourcedBase(TimestampedBase):
    """Base model for entities with source tracking"""

    source_name: str
    source_version: str | None = None
    device: str | None = None


# Main Models
class HealthData(SQLModel, table=True):
    """Root health data container"""

    id: int | None = Field(default=None, primary_key=True)
    locale: str
    export_date: datetime

    # Personal info
    date_of_birth: str
    biological_sex: str
    blood_type: str
    fitzpatrick_skin_type: str
    cardio_fitness_medications_use: str

    # Relationships
    records: list["Record"] = Relationship(back_populates="health_data")
    correlations: list["Correlation"] = Relationship(back_populates="health_data")
    workouts: list["Workout"] = Relationship(back_populates="health_data")
    activity_summaries: list["ActivitySummary"] = Relationship(
        back_populates="health_data"
    )
    clinical_records: list["ClinicalRecord"] = Relationship(
        back_populates="health_data"
    )
    audiograms: list["Audiogram"] = Relationship(back_populates="health_data")
    vision_prescriptions: list["VisionPrescription"] = Relationship(
        back_populates="health_data"
    )


class MetadataEntry(SQLModel, table=True):
    """Key-value metadata entries with proper polymorphic pattern"""

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    value: str

    # Polymorphic discriminator and ID
    parent_type: str = Field(index=True)  # 'record', 'correlation', 'workout', etc.
    parent_id: int = Field(index=True)


class CorrelationRecord(SQLModel, table=True):
    """Link table for Correlation-Record many-to-many"""

    correlation_id: int = Field(foreign_key="correlation.id", primary_key=True)
    record_id: int = Field(foreign_key="record.id", primary_key=True)


class Record(SourcedBase, table=True):
    """Generic health record"""

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)  # Indexed for filtering
    unit: str | None = None
    value: str | None = None

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="records")

    # Relationships
    heart_rate_variability_list: Optional["HeartRateVariabilityMetadataList"] = (
        Relationship(back_populates="record")
    )

    # Many-to-many with correlations
    correlations: list["Correlation"] = Relationship(
        back_populates="records", link_model=CorrelationRecord
    )


class Correlation(SourcedBase, table=True):
    """Groups related records together"""

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="correlations")

    # Relationships
    records: list[Record] = Relationship(
        back_populates="correlations", link_model=CorrelationRecord
    )


class HeartRateVariabilityMetadataList(SQLModel, table=True):
    """Container for HRV instantaneous readings"""

    id: int | None = Field(default=None, primary_key=True)

    # Foreign key
    record_id: int = Field(
        foreign_key="record.id", unique=True, index=True
    )  # One-to-one
    record: Record = Relationship(back_populates="heart_rate_variability_list")

    # Relationships
    instantaneous_bpm: list["InstantaneousBeatsPerMinute"] = Relationship(
        back_populates="hrv_list"
    )


class InstantaneousBeatsPerMinute(SQLModel, table=True):
    """Individual heart rate reading"""

    id: int | None = Field(default=None, primary_key=True)
    bpm: int
    time: datetime

    # Foreign key
    hrv_list_id: int = Field(
        foreign_key="heartratevariabilitymetadatalist.id", index=True
    )
    hrv_list: HeartRateVariabilityMetadataList = Relationship(
        back_populates="instantaneous_bpm"
    )


class Workout(SourcedBase, table=True):
    """Workout activity record"""

    id: int | None = Field(default=None, primary_key=True)
    workout_activity_type: str = Field(index=True)
    duration: float | None = None
    duration_unit: str | None = None
    total_distance: float | None = None
    total_distance_unit: str | None = None
    total_energy_burned: float | None = None
    total_energy_burned_unit: str | None = None

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="workouts")

    # Relationships
    events: list["WorkoutEvent"] = Relationship(back_populates="workout")
    statistics: list["WorkoutStatistics"] = Relationship(back_populates="workout")
    route: Optional["WorkoutRoute"] = Relationship(back_populates="workout")


class WorkoutEvent(SQLModel, table=True):
    """Event during a workout"""

    id: int | None = Field(default=None, primary_key=True)
    type: str
    date: datetime = Field(index=True)
    duration: float | None = None
    duration_unit: str | None = None

    # Foreign key
    workout_id: int = Field(foreign_key="workout.id", index=True)
    workout: Workout = Relationship(back_populates="events")


class WorkoutStatistics(TimestampedBase, table=True):
    """Statistics for a workout period"""

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    sum: float | None = None
    unit: str | None = None

    # Foreign key
    workout_id: int = Field(foreign_key="workout.id", index=True)
    workout: Workout = Relationship(back_populates="statistics")


class WorkoutRoute(SourcedBase, table=True):
    """GPS route for workout"""

    id: int | None = Field(default=None, primary_key=True)

    # Foreign key
    workout_id: int = Field(
        foreign_key="workout.id", unique=True, index=True
    )  # One-to-one
    workout: Workout = Relationship(back_populates="route")

    # File reference
    file_path: str | None = None


class ActivitySummary(SQLModel, table=True):
    """Daily activity summary"""

    id: int | None = Field(default=None, primary_key=True)
    date_components: str = Field(
        index=True, unique=True
    )  # Indexed and unique for date lookups
    active_energy_burned: float | None = None
    active_energy_burned_goal: float | None = None
    active_energy_burned_unit: str | None = None
    apple_move_time: float | None = None
    apple_move_time_goal: float | None = None
    apple_exercise_time: float | None = None
    apple_exercise_time_goal: float | None = None
    apple_stand_hours: int | None = None
    apple_stand_hours_goal: int | None = None

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="activity_summaries")


class ClinicalRecord(SQLModel, table=True):
    """FHIR clinical record reference"""

    id: int | None = Field(default=None, primary_key=True)
    type: str
    identifier: str
    source_name: str
    source_url: str
    fhir_version: str
    received_date: datetime
    resource_file_path: str

    # Foreign key
    health_data_id: int | None = Field(default=None, foreign_key="healthdata.id")
    health_data: HealthData | None = Relationship(back_populates="clinical_records")


class Audiogram(SourcedBase, table=True):
    """Hearing test data"""

    id: int | None = Field(default=None, primary_key=True)
    type: str

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="audiograms")

    # Relationships
    sensitivity_points: list["SensitivityPoint"] = Relationship(
        back_populates="audiogram"
    )


class SensitivityPoint(SQLModel, table=True):
    """Hearing sensitivity measurement"""

    id: int | None = Field(default=None, primary_key=True)
    frequency_value: float
    frequency_unit: str

    # Left ear measurements
    left_ear_value: float | None = None
    left_ear_unit: str | None = None
    left_ear_masked: bool | None = None
    left_ear_clamping_range_lower_bound: float | None = None
    left_ear_clamping_range_upper_bound: float | None = None

    # Right ear measurements
    right_ear_value: float | None = None
    right_ear_unit: str | None = None
    right_ear_masked: bool | None = None
    right_ear_clamping_range_lower_bound: float | None = None
    right_ear_clamping_range_upper_bound: float | None = None

    # Foreign key
    audiogram_id: int = Field(foreign_key="audiogram.id", index=True)
    audiogram: Audiogram = Relationship(back_populates="sensitivity_points")


class VisionPrescription(SQLModel, table=True):
    """Eye prescription data"""

    id: int | None = Field(default=None, primary_key=True)
    type: str
    date_issued: datetime
    expiration_date: datetime | None = None
    brand: str | None = None

    # Foreign key
    health_data_id: int | None = Field(
        default=None, foreign_key="healthdata.id", index=True
    )
    health_data: HealthData | None = Relationship(back_populates="vision_prescriptions")

    # Relationships
    eye_prescriptions: list["EyePrescription"] = Relationship(
        back_populates="vision_prescription"
    )
    attachments: list["VisionAttachment"] = Relationship(
        back_populates="vision_prescription"
    )


class EyePrescription(SQLModel, table=True):
    """Individual eye prescription data"""

    id: int | None = Field(default=None, primary_key=True)
    eye_side: EyeSide

    # Prescription values
    sphere: float | None = None
    sphere_unit: str | None = None
    cylinder: float | None = None
    cylinder_unit: str | None = None
    axis: float | None = None
    axis_unit: str | None = None
    add: float | None = None
    add_unit: str | None = None
    vertex: float | None = None
    vertex_unit: str | None = None
    prism_amount: float | None = None
    prism_amount_unit: str | None = None
    prism_angle: float | None = None
    prism_angle_unit: str | None = None
    far_pd: float | None = None
    far_pd_unit: str | None = None
    near_pd: float | None = None
    near_pd_unit: str | None = None
    base_curve: float | None = None
    base_curve_unit: str | None = None
    diameter: float | None = None
    diameter_unit: str | None = None

    # Foreign key
    vision_prescription_id: int = Field(foreign_key="visionprescription.id", index=True)
    vision_prescription: VisionPrescription = Relationship(
        back_populates="eye_prescriptions"
    )


class VisionAttachment(SQLModel, table=True):
    """Attachment reference for vision prescription"""

    id: int | None = Field(default=None, primary_key=True)
    identifier: str | None = None

    # Foreign key
    vision_prescription_id: int = Field(foreign_key="visionprescription.id", index=True)
    vision_prescription: VisionPrescription = Relationship(back_populates="attachments")
