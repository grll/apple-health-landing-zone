import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lxml import etree  # type: ignore[import-untyped]
from sqlmodel import Session, SQLModel, create_engine, select
from tqdm import tqdm

from .models import (
    ActivitySummary,
    Audiogram,
    ClinicalRecord,
    Correlation,
    CorrelationRecord,
    EyePrescription,
    EyeSide,
    HealthData,
    HeartRateVariabilityMetadataList,
    InstantaneousBeatsPerMinute,
    MetadataEntry,
    Record,
    SensitivityPoint,
    VisionAttachment,
    VisionPrescription,
    Workout,
    WorkoutEvent,
    WorkoutRoute,
    WorkoutStatistics,
)


class AppleHealthParser:
    """Parser for Apple Health export XML files with streaming support."""

    def __init__(self, db_path: str = "data/sqlite.db", bulk_mode: bool = True, data_cutoff: timedelta = timedelta(days=180)):
        """Initialize parser with database connection.
        
        Args:
            db_path: Path to SQLite database
            bulk_mode: Enable bulk processing for better performance
            data_cutoff: Only process records newer than this timedelta (default: 6 months)
        """
        # Create data directory if it doesn't exist
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Create database engine
        self.engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self.engine)

        # Add performance indexes
        self._create_indexes()

        # Batch processing settings
        self.bulk_mode = bulk_mode
        self.batch_size = 5000 if bulk_mode else 1000
        self.transaction_batch_size = 100  # Commit every N entities
        self.current_batch: list[Any] = []
        self.pending_commits = 0

        # Data filtering settings
        self.data_cutoff = data_cutoff
        self.cutoff_date = datetime.now(ZoneInfo("Europe/Zurich")) - data_cutoff

        # Bulk processing collections
        self.records_batch: list[Record] = []
        self.workouts_batch: list[Workout] = []
        self.correlations_batch: list[Correlation] = []
        self.metadata_batch: list[MetadataEntry] = []

        # Maps for deferred ID resolution
        self.record_temp_ids: dict[str, int] = {}  # temp_id -> actual_id
        self.workout_temp_ids: dict[str, int] = {}
        self.correlation_temp_ids: dict[str, int] = {}
        self.temp_id_counter = 0

        self.stats = {
            "records": 0,
            "workouts": 0,
            "correlations": 0,
            "activity_summaries": 0,
            "clinical_records": 0,
            "audiograms": 0,
            "vision_prescriptions": 0,
            "metadata_entries": 0,
            "hrv_lists": 0,
            "correlation_records": 0,
            "errors": 0,
            "duplicates": 0,
            "filtered_old": 0,
        }

    def parse_file(self, xml_path: str) -> None:
        """Parse Apple Health export XML file using streaming."""
        print(f"Starting to parse: {xml_path}")

        # Check if file exists
        if not os.path.exists(xml_path):
            raise FileNotFoundError(f"XML file not found: {xml_path}")

        # Get file size for progress tracking
        file_size = os.path.getsize(xml_path)
        print(f"File size: {file_size / (1024**3):.2f} GB")

        # Clear events to free memory during parsing
        context = etree.iterparse(
            xml_path,
            events=("start", "end"),
            tag=None,  # Process all tags
            huge_tree=True,  # Enable parsing of large files
        )

        # Make iterator return start-end event pairs
        context = iter(context)

        # Skip to root element
        event, root = next(context)

        # Current element being processed
        health_data: HealthData | None = None
        current_correlation: Correlation | None = None
        current_workout: Workout | None = None
        current_audiogram: Audiogram | None = None
        current_vision_prescription: VisionPrescription | None = None
        current_record: Record | None = None
        current_hrv_list: HeartRateVariabilityMetadataList | None = None

        # Track parent elements for metadata
        current_parent_type: str | None = None
        current_parent_id: int | None = None

        with Session(self.engine) as session:
            try:
                # Process root element first
                if root.tag == "HealthData":
                    # Check if HealthData already exists
                    existing_health_data = session.exec(select(HealthData)).first()
                    if existing_health_data:
                        health_data = existing_health_data
                        print(
                            f"Using existing HealthData record with ID: {health_data.id}"
                        )
                    else:
                        health_data = self._parse_health_data(root)
                        session.add(health_data)
                        session.commit()
                        print(f"Created HealthData record with ID: {health_data.id}")

                # Create progress bar
                pbar = tqdm(desc="Processing", unit=" elements", miniters=1000)

                for event, elem in context:
                    if event == "start":
                        # Update progress bar
                        pbar.update(1)

                        # Update description with current stats every 5000 records
                        total_processed = (
                            self.stats["records"] + self.stats["duplicates"] + self.stats["filtered_old"]
                        )
                        if total_processed % 5000 == 0 and total_processed > 0:
                            pbar.set_description(
                                f"Records: {self.stats['records']:,} | "
                                f"Duplicates: {self.stats['duplicates']:,} | "
                                f"Filtered: {self.stats['filtered_old']:,} | "
                                f"Errors: {self.stats['errors']:,}"
                            )

                        try:
                            if elem.tag == "HealthData" and not health_data:
                                # Check if HealthData already exists
                                existing_health_data = session.exec(
                                    select(HealthData)
                                ).first()
                                if existing_health_data:
                                    health_data = existing_health_data
                                else:
                                    health_data = self._parse_health_data(elem)
                                    session.add(health_data)
                                    session.commit()

                            elif elem.tag == "ExportDate" and health_data:
                                # Update health_data with export date
                                export_date_str = elem.get("value")
                                if export_date_str:
                                    health_data.export_date = self._parse_datetime(
                                        export_date_str
                                    )
                                    session.add(health_data)
                                    session.commit()

                            elif elem.tag == "Me" and health_data:
                                # Update health_data with personal info
                                health_data.date_of_birth = elem.get(
                                    "HKCharacteristicTypeIdentifierDateOfBirth", ""
                                )
                                health_data.biological_sex = elem.get(
                                    "HKCharacteristicTypeIdentifierBiologicalSex", ""
                                )
                                health_data.blood_type = elem.get(
                                    "HKCharacteristicTypeIdentifierBloodType", ""
                                )
                                health_data.fitzpatrick_skin_type = elem.get(
                                    "HKCharacteristicTypeIdentifierFitzpatrickSkinType",
                                    "",
                                )
                                health_data.cardio_fitness_medications_use = elem.get(
                                    "HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse",
                                    "",
                                )
                                session.add(health_data)
                                session.commit()

                            elif (
                                elem.tag == "Record" and health_data and health_data.id
                            ):
                                record = self._parse_record(elem, health_data.id)

                                # Filter by cutoff date
                                if record.start_date < self.cutoff_date:
                                    self.stats["filtered_old"] += 1
                                    continue

                                # Check if inside a correlation - always use individual processing
                                if current_correlation and current_correlation.id:
                                    existing = self._check_duplicate_record(
                                        session, record
                                    )
                                    if existing:
                                        self.stats["duplicates"] += 1
                                        record = existing
                                    else:
                                        session.add(record)
                                        session.commit()

                                    if record.id:
                                        existing_link = (
                                            self._check_duplicate_correlation_record(
                                                session,
                                                current_correlation.id,
                                                record.id,
                                            )
                                        )
                                        if not existing_link:
                                            link = CorrelationRecord(
                                                correlation_id=current_correlation.id,
                                                record_id=record.id,
                                            )
                                            self._add_to_batch(session, link)
                                            self.stats["correlation_records"] += 1
                                else:
                                    # Regular records - check for duplicate and use batched commits
                                    existing = self._check_duplicate_record(
                                        session, record
                                    )
                                    if existing:
                                        self.stats["duplicates"] += 1
                                        current_record = existing
                                        current_parent_type = "record"
                                        current_parent_id = existing.id
                                    else:
                                        session.add(record)
                                        self.pending_commits += 1
                                        current_record = record
                                        current_parent_type = "record"
                                        # Defer commit for batching
                                        if (
                                            self.pending_commits
                                            >= self.transaction_batch_size
                                        ):
                                            session.commit()
                                            self.pending_commits = 0
                                        else:
                                            session.flush()  # Get ID without committing
                                        current_parent_id = record.id
                                        self.stats["records"] += 1

                            elif (
                                elem.tag == "Correlation"
                                and health_data
                                and health_data.id
                            ):
                                correlation = self._parse_correlation(
                                    elem, health_data.id
                                )

                                # Filter by cutoff date
                                if correlation.start_date < self.cutoff_date:
                                    self.stats["filtered_old"] += 1
                                    continue

                                # Check for duplicate
                                existing = self._check_duplicate_correlation(
                                    session, correlation
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                    current_correlation = existing
                                else:
                                    session.add(correlation)
                                    self.pending_commits += 1
                                    current_correlation = correlation
                                    # Defer commit for batching
                                    if (
                                        self.pending_commits
                                        >= self.transaction_batch_size
                                    ):
                                        session.commit()
                                        self.pending_commits = 0
                                    else:
                                        session.flush()  # Get ID without committing
                                    self.stats["correlations"] += 1

                                current_parent_type = "correlation"
                                current_parent_id = current_correlation.id

                            elif (
                                elem.tag == "Workout" and health_data and health_data.id
                            ):
                                workout = self._parse_workout(elem, health_data.id)

                                # Filter by cutoff date
                                if workout.start_date < self.cutoff_date:
                                    self.stats["filtered_old"] += 1
                                    continue

                                # Check for duplicate
                                existing = self._check_duplicate_workout(
                                    session, workout
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                    current_workout = existing
                                else:
                                    session.add(workout)
                                    self.pending_commits += 1
                                    current_workout = workout
                                    # Defer commit for batching
                                    if (
                                        self.pending_commits
                                        >= self.transaction_batch_size
                                    ):
                                        session.commit()
                                        self.pending_commits = 0
                                    else:
                                        session.flush()  # Get ID without committing
                                    self.stats["workouts"] += 1

                                current_parent_type = "workout"
                                current_parent_id = current_workout.id

                            elif (
                                elem.tag == "ActivitySummary"
                                and health_data
                                and health_data.id
                            ):
                                summary = self._parse_activity_summary(
                                    elem, health_data.id
                                )

                                # Check for duplicate
                                existing = self._check_duplicate_activity_summary(
                                    session, summary
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                else:
                                    self._add_to_batch(session, summary)
                                    self.stats["activity_summaries"] += 1

                            elif (
                                elem.tag == "ClinicalRecord"
                                and health_data
                                and health_data.id
                            ):
                                clinical = self._parse_clinical_record(
                                    elem, health_data.id
                                )

                                # Check for duplicate
                                existing = self._check_duplicate_clinical_record(
                                    session, clinical
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                else:
                                    self._add_to_batch(session, clinical)
                                    self.stats["clinical_records"] += 1

                            elif (
                                elem.tag == "Audiogram"
                                and health_data
                                and health_data.id
                            ):
                                audiogram = self._parse_audiogram(elem, health_data.id)

                                # Filter by cutoff date
                                if audiogram.start_date < self.cutoff_date:
                                    self.stats["filtered_old"] += 1
                                    continue

                                # Check for duplicate
                                existing = self._check_duplicate_audiogram(
                                    session, audiogram
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                    current_audiogram = existing
                                else:
                                    session.add(audiogram)
                                    session.commit()
                                    current_audiogram = audiogram
                                    self.stats["audiograms"] += 1

                            elif (
                                elem.tag == "VisionPrescription"
                                and health_data
                                and health_data.id
                            ):
                                prescription = self._parse_vision_prescription(
                                    elem, health_data.id
                                )

                                # Check for duplicate
                                existing = self._check_duplicate_vision_prescription(
                                    session, prescription
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                    current_vision_prescription = existing
                                else:
                                    session.add(prescription)
                                    session.commit()
                                    current_vision_prescription = prescription
                                    self.stats["vision_prescriptions"] += 1

                            elif (
                                elem.tag == "MetadataEntry"
                                and current_parent_type
                                and current_parent_id
                            ):
                                metadata = self._parse_metadata_entry(
                                    elem, current_parent_type, current_parent_id
                                )
                                self._add_to_batch(session, metadata)
                                self.stats["metadata_entries"] += 1

                            elif (
                                elem.tag == "HeartRateVariabilityMetadataList"
                                and current_record
                                and current_record.id
                            ):
                                # Check for existing HRV list
                                existing_hrv = self._check_duplicate_hrv_list(
                                    session, current_record.id
                                )
                                if existing_hrv:
                                    current_hrv_list = existing_hrv
                                    self.stats["duplicates"] += 1
                                else:
                                    current_hrv_list = self._parse_hrv_list(
                                        current_record.id
                                    )
                                    session.add(current_hrv_list)
                                    session.commit()  # Need ID for relationships
                                    self.stats["hrv_lists"] += 1

                            # Handle nested elements
                            elif (
                                elem.tag == "WorkoutEvent"
                                and current_workout
                                and current_workout.id
                            ):
                                event_obj = self._parse_workout_event(
                                    elem, current_workout.id
                                )
                                self._add_to_batch(session, event_obj)

                            elif (
                                elem.tag == "WorkoutStatistics"
                                and current_workout
                                and current_workout.id
                            ):
                                stat = self._parse_workout_statistics(
                                    elem, current_workout.id
                                )
                                self._add_to_batch(session, stat)

                            elif (
                                elem.tag == "WorkoutRoute"
                                and current_workout
                                and current_workout.id
                            ):
                                route = self._parse_workout_route(
                                    elem, current_workout.id
                                )

                                # Check for duplicate WorkoutRoute
                                existing = self._check_duplicate_workout_route(
                                    session, route
                                )
                                if existing:
                                    self.stats["duplicates"] += 1
                                else:
                                    session.add(route)
                                    session.commit()  # Immediate commit due to unique constraint

                            elif (
                                elem.tag == "SensitivityPoint"
                                and current_audiogram
                                and current_audiogram.id
                            ):
                                point = self._parse_sensitivity_point(
                                    elem, current_audiogram.id
                                )
                                self._add_to_batch(session, point)

                            elif (
                                elem.tag == "Prescription"
                                and current_vision_prescription
                                and current_vision_prescription.id
                            ):
                                prescription = self._parse_eye_prescription(
                                    elem, current_vision_prescription.id
                                )
                                self._add_to_batch(session, prescription)

                            elif (
                                elem.tag == "Attachment"
                                and current_vision_prescription
                                and current_vision_prescription.id
                            ):
                                attachment = self._parse_vision_attachment(
                                    elem, current_vision_prescription.id
                                )
                                self._add_to_batch(session, attachment)

                            elif (
                                elem.tag == "InstantaneousBeatsPerMinute"
                                and current_hrv_list
                                and current_hrv_list.id
                            ):
                                bpm = self._parse_instantaneous_bpm(
                                    elem, current_hrv_list.id, current_record.start_date if current_record else None
                                )
                                self._add_to_batch(session, bpm)

                        except Exception as e:
                            self.stats["errors"] += 1
                            if self.stats["errors"] <= 10:  # Only print first 10 errors
                                print(f"Error parsing {elem.tag}: {e}")

                    elif event == "end":
                        # Clear completed elements
                        if elem.tag == "Correlation":
                            current_correlation = None
                            current_parent_type = None
                            current_parent_id = None
                        elif elem.tag == "Workout":
                            current_workout = None
                            current_parent_type = None
                            current_parent_id = None
                        elif elem.tag == "Audiogram":
                            current_audiogram = None
                        elif elem.tag == "VisionPrescription":
                            current_vision_prescription = None
                        elif elem.tag == "Record" and not current_correlation:
                            current_record = None
                            current_parent_type = None
                            current_parent_id = None
                        elif elem.tag == "HeartRateVariabilityMetadataList":
                            current_hrv_list = None

                        # Clear the element to free memory
                        elem.clear()
                        # Also remove preceding siblings
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]

                # Final commit for any pending transactions
                if self.pending_commits > 0:
                    session.commit()
                    self.pending_commits = 0

                # Flush any remaining batches
                self._flush_all_batches(session)
                pbar.close()

            except Exception as e:
                pbar.close()
                print(f"Fatal error during parsing: {e}")
                raise

        # Final statistics
        self._print_progress()
        print(f"Parsing complete! Data cutoff: {self.cutoff_date.isoformat()}")

    def _add_to_batch(self, session: Session, obj: Any) -> None:
        """Add object to batch and flush if necessary."""
        self.current_batch.append(obj)
        if len(self.current_batch) >= self.batch_size:
            self._flush_batch(session)

    def _flush_batch(self, session: Session) -> None:
        """Flush current batch to database."""
        if self.current_batch:
            session.add_all(self.current_batch)
            session.commit()
            self.current_batch = []

    def _create_indexes(self) -> None:
        """Create database indexes for performance."""
        from sqlalchemy import text

        with Session(self.engine) as session:
            # Indexes for duplicate checking
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_record_duplicate ON record (type, start_date, end_date, health_data_id, value)",
                "CREATE INDEX IF NOT EXISTS idx_workout_duplicate ON workout (workout_activity_type, start_date, end_date, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_correlation_duplicate ON correlation (type, start_date, end_date, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_activity_summary_duplicate ON activitysummary (date_components, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_clinical_record_duplicate ON clinicalrecord (identifier, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_audiogram_duplicate ON audiogram (type, start_date, end_date, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_vision_prescription_duplicate ON visionprescription (type, date_issued, health_data_id)",
                "CREATE INDEX IF NOT EXISTS idx_correlation_record_duplicate ON correlationrecord (correlation_id, record_id)",
            ]
            for index_sql in indexes:
                try:
                    session.execute(text(index_sql))
                except Exception as e:
                    print(f"Index creation warning: {e}")
            session.commit()

    def _bulk_insert_records(self, session: Session) -> None:
        """Bulk insert records with batch duplicate checking."""
        if not self.records_batch:
            return

        # Group records by type for efficient duplicate checking
        records_by_type: dict[tuple[str | None, int], list[Record]] = {}
        for record in self.records_batch:
            key = (record.type, record.health_data_id or 0)
            if key not in records_by_type:
                records_by_type[key] = []
            records_by_type[key].append(record)

        new_records = []
        for (record_type, health_data_id), type_records in records_by_type.items():
            # Batch check for existing records of this type
            start_dates = [r.start_date for r in type_records]
            end_dates = [r.end_date for r in type_records]

            # Build query conditions
            stmt = select(Record).where(
                Record.type == record_type,
                Record.health_data_id == health_data_id,
            )

            if start_dates:
                from sqlalchemy import or_

                date_conditions = []
                for i, (start_date, end_date) in enumerate(zip(start_dates, end_dates)):
                    date_conditions.append(
                        (Record.start_date == start_date)
                        & (Record.end_date == end_date)
                    )
                if date_conditions:
                    stmt = stmt.where(or_(*date_conditions))

            existing_records = session.exec(stmt).all()

            # Create lookup set for existing records
            existing_set: set[tuple[datetime, datetime, str | None]] = set()
            for existing in existing_records:
                lookup_key = (existing.start_date, existing.end_date, existing.value)
                existing_set.add(lookup_key)

            # Filter out duplicates
            for record in type_records:
                record_key = (record.start_date, record.end_date, record.value)
                if record_key in existing_set:
                    self.stats["duplicates"] += 1
                else:
                    new_records.append(record)

        if new_records:
            session.add_all(new_records)
            session.commit()
            self.stats["records"] += len(new_records)

        self.records_batch = []

    def _bulk_insert_workouts(self, session: Session) -> None:
        """Bulk insert workouts with duplicate checking."""
        if not self.workouts_batch:
            return

        new_workouts = []
        for workout in self.workouts_batch:
            existing = self._check_duplicate_workout(session, workout)
            if existing:
                self.stats["duplicates"] += 1
            else:
                new_workouts.append(workout)

        if new_workouts:
            session.add_all(new_workouts)
            session.commit()
            self.stats["workouts"] += len(new_workouts)

        self.workouts_batch = []

    def _bulk_insert_correlations(self, session: Session) -> None:
        """Bulk insert correlations with duplicate checking."""
        if not self.correlations_batch:
            return

        new_correlations = []
        for correlation in self.correlations_batch:
            existing = self._check_duplicate_correlation(session, correlation)
            if existing:
                self.stats["duplicates"] += 1
            else:
                new_correlations.append(correlation)

        if new_correlations:
            session.add_all(new_correlations)
            session.commit()
            self.stats["correlations"] += len(new_correlations)

        self.correlations_batch = []

    def _flush_all_batches(self, session: Session) -> None:
        """Flush all bulk batches to database."""
        if self.bulk_mode:
            self._bulk_insert_records(session)
            self._bulk_insert_workouts(session)
            self._bulk_insert_correlations(session)
            session.commit()
        self._flush_batch(session)  # Handle remaining objects

    def _print_progress(self) -> None:
        """Print current parsing progress."""
        print("Final Statistics:")
        for key, value in self.stats.items():
            print(f"  {key}: {value:,}")

    # Duplicate checking methods
    def _check_duplicate_record(
        self, session: Session, record: Record
    ) -> Record | None:
        """Check if a record already exists."""
        stmt = select(Record).where(
            Record.type == record.type,
            Record.start_date == record.start_date,
            Record.end_date == record.end_date,
            Record.health_data_id == record.health_data_id,
        )

        # Also check value if present
        if record.value is not None:
            stmt = stmt.where(Record.value == record.value)
        else:
            stmt = stmt.where(Record.value.is_(None))

        return session.exec(stmt).first()

    def _check_duplicate_workout(
        self, session: Session, workout: Workout
    ) -> Workout | None:
        """Check if a workout already exists."""
        return session.exec(
            select(Workout).where(
                Workout.workout_activity_type == workout.workout_activity_type,
                Workout.start_date == workout.start_date,
                Workout.end_date == workout.end_date,
                Workout.health_data_id == workout.health_data_id,
            )
        ).first()

    def _check_duplicate_correlation(
        self, session: Session, correlation: Correlation
    ) -> Correlation | None:
        """Check if a correlation already exists."""
        return session.exec(
            select(Correlation).where(
                Correlation.type == correlation.type,
                Correlation.start_date == correlation.start_date,
                Correlation.end_date == correlation.end_date,
                Correlation.health_data_id == correlation.health_data_id,
            )
        ).first()

    def _check_duplicate_activity_summary(
        self, session: Session, summary: ActivitySummary
    ) -> ActivitySummary | None:
        """Check if an activity summary already exists."""
        return session.exec(
            select(ActivitySummary).where(
                ActivitySummary.date_components == summary.date_components,
                ActivitySummary.health_data_id == summary.health_data_id,
            )
        ).first()

    def _check_duplicate_clinical_record(
        self, session: Session, record: ClinicalRecord
    ) -> ClinicalRecord | None:
        """Check if a clinical record already exists."""
        return session.exec(
            select(ClinicalRecord).where(
                ClinicalRecord.identifier == record.identifier,
                ClinicalRecord.health_data_id == record.health_data_id,
            )
        ).first()

    def _check_duplicate_audiogram(
        self, session: Session, audiogram: Audiogram
    ) -> Audiogram | None:
        """Check if an audiogram already exists."""
        return session.exec(
            select(Audiogram).where(
                Audiogram.type == audiogram.type,
                Audiogram.start_date == audiogram.start_date,
                Audiogram.end_date == audiogram.end_date,
                Audiogram.health_data_id == audiogram.health_data_id,
            )
        ).first()

    def _check_duplicate_vision_prescription(
        self, session: Session, prescription: VisionPrescription
    ) -> VisionPrescription | None:
        """Check if a vision prescription already exists."""
        return session.exec(
            select(VisionPrescription).where(
                VisionPrescription.type == prescription.type,
                VisionPrescription.date_issued == prescription.date_issued,
                VisionPrescription.health_data_id == prescription.health_data_id,
            )
        ).first()

    def _check_duplicate_correlation_record(
        self, session: Session, correlation_id: int, record_id: int
    ) -> CorrelationRecord | None:
        """Check if a correlation-record link already exists."""
        return session.exec(
            select(CorrelationRecord).where(
                CorrelationRecord.correlation_id == correlation_id,
                CorrelationRecord.record_id == record_id,
            )
        ).first()

    def _check_duplicate_workout_route(
        self, session: Session, route: WorkoutRoute
    ) -> WorkoutRoute | None:
        """Check if a workout route already exists."""
        return session.exec(
            select(WorkoutRoute).where(
                WorkoutRoute.workout_id == route.workout_id,
            )
        ).first()

    def _check_duplicate_hrv_list(
        self, session: Session, record_id: int
    ) -> HeartRateVariabilityMetadataList | None:
        """Check if an HRV list already exists for this record."""
        return session.exec(
            select(HeartRateVariabilityMetadataList).where(
                HeartRateVariabilityMetadataList.record_id == record_id,
            )
        ).first()

    # Parsing methods remain the same
    def _parse_datetime(self, date_str: str, base_date: datetime | None = None) -> datetime:
        """Parse datetime string from Apple Health format.
        
        Args:
            date_str: The datetime or time string to parse
            base_date: Base date to use for time-only strings (for InstantaneousBeatsPerMinute)
        """
        # Check if this is a time-only format like "7:47:41.86 PM"
        if base_date and ("AM" in date_str or "PM" in date_str) and ":" in date_str and "-" not in date_str:
            # Parse time-only format and combine with base date
            try:
                # Handle formats like "7:47:41.86 PM"
                time_part = datetime.strptime(date_str, "%I:%M:%S.%f %p").time()
            except ValueError:
                try:
                    # Fallback for formats like "7:47:41 PM" (no microseconds)
                    time_part = datetime.strptime(date_str, "%I:%M:%S %p").time()
                except ValueError:
                    # If all fails, try without seconds
                    time_part = datetime.strptime(date_str, "%I:%M %p").time()
            
            # Combine with base date
            combined = datetime.combine(base_date.date(), time_part)
            # Use the same timezone as base_date
            return combined.replace(tzinfo=base_date.tzinfo)
        else:
            # Apple Health standard format: "2023-12-31 23:59:59 +0000"
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
            # Convert to preferred timezone
            return dt.astimezone(ZoneInfo("Europe/Zurich"))

    def _parse_health_data(self, elem: Any) -> HealthData:
        """Parse HealthData root element."""
        # HealthData only has locale attribute
        # ExportDate and Me are child elements that we'll handle separately
        return HealthData(
            locale=elem.get("locale", ""),
            export_date=datetime.now(
                ZoneInfo("Europe/Zurich")
            ),  # Will be updated by ExportDate element
            date_of_birth="",  # Will be updated by Me element
            biological_sex="",  # Will be updated by Me element
            blood_type="",  # Will be updated by Me element
            fitzpatrick_skin_type="",  # Will be updated by Me element
            cardio_fitness_medications_use="",  # Will be updated by Me element
        )

    def _parse_record(self, elem: Any, health_data_id: int) -> Record:
        """Parse Record element."""
        return Record(
            type=elem.get("type"),
            source_name=elem.get("sourceName"),
            source_version=elem.get("sourceVersion"),
            device=elem.get("device"),
            unit=elem.get("unit"),
            value=elem.get("value"),
            creation_date=self._parse_datetime(elem.get("creationDate"))
            if elem.get("creationDate")
            else None,
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            health_data_id=health_data_id,
        )

    def _parse_correlation(self, elem: Any, health_data_id: int) -> Correlation:
        """Parse Correlation element."""
        return Correlation(
            type=elem.get("type"),
            source_name=elem.get("sourceName"),
            source_version=elem.get("sourceVersion"),
            device=elem.get("device"),
            creation_date=self._parse_datetime(elem.get("creationDate"))
            if elem.get("creationDate")
            else None,
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            health_data_id=health_data_id,
        )

    def _parse_workout(self, elem: Any, health_data_id: int) -> Workout:
        """Parse Workout element."""
        return Workout(
            workout_activity_type=elem.get("workoutActivityType"),
            duration=float(elem.get("duration")) if elem.get("duration") else None,
            duration_unit=elem.get("durationUnit"),
            total_distance=float(elem.get("totalDistance"))
            if elem.get("totalDistance")
            else None,
            total_distance_unit=elem.get("totalDistanceUnit"),
            total_energy_burned=float(elem.get("totalEnergyBurned"))
            if elem.get("totalEnergyBurned")
            else None,
            total_energy_burned_unit=elem.get("totalEnergyBurnedUnit"),
            source_name=elem.get("sourceName"),
            source_version=elem.get("sourceVersion"),
            device=elem.get("device"),
            creation_date=self._parse_datetime(elem.get("creationDate"))
            if elem.get("creationDate")
            else None,
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            health_data_id=health_data_id,
        )

    def _parse_activity_summary(
        self, elem: Any, health_data_id: int
    ) -> ActivitySummary:
        """Parse ActivitySummary element."""
        return ActivitySummary(
            date_components=elem.get("dateComponents"),
            active_energy_burned=float(elem.get("activeEnergyBurned"))
            if elem.get("activeEnergyBurned")
            else None,
            active_energy_burned_goal=float(elem.get("activeEnergyBurnedGoal"))
            if elem.get("activeEnergyBurnedGoal")
            else None,
            active_energy_burned_unit=elem.get("activeEnergyBurnedUnit"),
            apple_move_time=float(elem.get("appleMoveTime"))
            if elem.get("appleMoveTime")
            else None,
            apple_move_time_goal=float(elem.get("appleMoveTimeGoal"))
            if elem.get("appleMoveTimeGoal")
            else None,
            apple_exercise_time=float(elem.get("appleExerciseTime"))
            if elem.get("appleExerciseTime")
            else None,
            apple_exercise_time_goal=float(elem.get("appleExerciseTimeGoal"))
            if elem.get("appleExerciseTimeGoal")
            else None,
            apple_stand_hours=int(elem.get("appleStandHours"))
            if elem.get("appleStandHours")
            else None,
            apple_stand_hours_goal=int(elem.get("appleStandHoursGoal"))
            if elem.get("appleStandHoursGoal")
            else None,
            health_data_id=health_data_id,
        )

    def _parse_clinical_record(self, elem: Any, health_data_id: int) -> ClinicalRecord:
        """Parse ClinicalRecord element."""
        return ClinicalRecord(
            type=elem.get("type"),
            identifier=elem.get("identifier"),
            source_name=elem.get("sourceName"),
            source_url=elem.get("sourceURL"),
            fhir_version=elem.get("fhirVersion"),
            received_date=self._parse_datetime(elem.get("receivedDate")),
            resource_file_path=elem.get("resourceFilePath"),
            health_data_id=health_data_id,
        )

    def _parse_audiogram(self, elem: Any, health_data_id: int) -> Audiogram:
        """Parse Audiogram element."""
        return Audiogram(
            type=elem.get("type"),
            source_name=elem.get("sourceName"),
            source_version=elem.get("sourceVersion"),
            device=elem.get("device"),
            creation_date=self._parse_datetime(elem.get("creationDate"))
            if elem.get("creationDate")
            else None,
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            health_data_id=health_data_id,
        )

    def _parse_vision_prescription(
        self, elem: Any, health_data_id: int
    ) -> VisionPrescription:
        """Parse VisionPrescription element."""
        return VisionPrescription(
            type=elem.get("type"),
            date_issued=self._parse_datetime(elem.get("dateIssued")),
            expiration_date=self._parse_datetime(elem.get("expirationDate"))
            if elem.get("expirationDate")
            else None,
            brand=elem.get("brand"),
            health_data_id=health_data_id,
        )

    def _parse_workout_event(self, elem: Any, workout_id: int) -> WorkoutEvent:
        """Parse WorkoutEvent element."""
        return WorkoutEvent(
            type=elem.get("type"),
            date=self._parse_datetime(elem.get("date")),
            duration=float(elem.get("duration")) if elem.get("duration") else None,
            duration_unit=elem.get("durationUnit"),
            workout_id=workout_id,
        )

    def _parse_workout_statistics(
        self, elem: Any, workout_id: int
    ) -> WorkoutStatistics:
        """Parse WorkoutStatistics element."""
        return WorkoutStatistics(
            type=elem.get("type"),
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            average=float(elem.get("average")) if elem.get("average") else None,
            minimum=float(elem.get("minimum")) if elem.get("minimum") else None,
            maximum=float(elem.get("maximum")) if elem.get("maximum") else None,
            sum=float(elem.get("sum")) if elem.get("sum") else None,
            unit=elem.get("unit"),
            workout_id=workout_id,
        )

    def _parse_workout_route(self, elem: Any, workout_id: int) -> WorkoutRoute:
        """Parse WorkoutRoute element."""
        return WorkoutRoute(
            source_name=elem.get("sourceName"),
            source_version=elem.get("sourceVersion"),
            device=elem.get("device"),
            creation_date=self._parse_datetime(elem.get("creationDate"))
            if elem.get("creationDate")
            else None,
            start_date=self._parse_datetime(elem.get("startDate")),
            end_date=self._parse_datetime(elem.get("endDate")),
            file_path=elem.get("filePath"),
            workout_id=workout_id,
        )

    def _parse_sensitivity_point(
        self, elem: Any, audiogram_id: int
    ) -> SensitivityPoint:
        """Parse SensitivityPoint element."""
        return SensitivityPoint(
            frequency_value=float(elem.get("frequencyValue")),
            frequency_unit=elem.get("frequencyUnit"),
            left_ear_value=float(elem.get("leftEarValue"))
            if elem.get("leftEarValue")
            else None,
            left_ear_unit=elem.get("leftEarUnit"),
            left_ear_masked=elem.get("leftEarMasked") == "true"
            if elem.get("leftEarMasked")
            else None,
            left_ear_clamping_range_lower_bound=float(
                elem.get("leftEarClampingRangeLowerBound")
            )
            if elem.get("leftEarClampingRangeLowerBound")
            else None,
            left_ear_clamping_range_upper_bound=float(
                elem.get("leftEarClampingRangeUpperBound")
            )
            if elem.get("leftEarClampingRangeUpperBound")
            else None,
            right_ear_value=float(elem.get("rightEarValue"))
            if elem.get("rightEarValue")
            else None,
            right_ear_unit=elem.get("rightEarUnit"),
            right_ear_masked=elem.get("rightEarMasked") == "true"
            if elem.get("rightEarMasked")
            else None,
            right_ear_clamping_range_lower_bound=float(
                elem.get("rightEarClampingRangeLowerBound")
            )
            if elem.get("rightEarClampingRangeLowerBound")
            else None,
            right_ear_clamping_range_upper_bound=float(
                elem.get("rightEarClampingRangeUpperBound")
            )
            if elem.get("rightEarClampingRangeUpperBound")
            else None,
            audiogram_id=audiogram_id,
        )

    def _parse_eye_prescription(
        self, elem: Any, vision_prescription_id: int
    ) -> EyePrescription:
        """Parse Prescription (eye) element."""
        eye_side = EyeSide.LEFT if elem.get("eye") == "left" else EyeSide.RIGHT

        return EyePrescription(
            eye_side=eye_side,
            sphere=float(elem.get("sphere")) if elem.get("sphere") else None,
            sphere_unit=elem.get("sphereUnit"),
            cylinder=float(elem.get("cylinder")) if elem.get("cylinder") else None,
            cylinder_unit=elem.get("cylinderUnit"),
            axis=float(elem.get("axis")) if elem.get("axis") else None,
            axis_unit=elem.get("axisUnit"),
            add=float(elem.get("add")) if elem.get("add") else None,
            add_unit=elem.get("addUnit"),
            vertex=float(elem.get("vertex")) if elem.get("vertex") else None,
            vertex_unit=elem.get("vertexUnit"),
            prism_amount=float(elem.get("prismAmount"))
            if elem.get("prismAmount")
            else None,
            prism_amount_unit=elem.get("prismAmountUnit"),
            prism_angle=float(elem.get("prismAngle"))
            if elem.get("prismAngle")
            else None,
            prism_angle_unit=elem.get("prismAngleUnit"),
            far_pd=float(elem.get("farPD")) if elem.get("farPD") else None,
            far_pd_unit=elem.get("farPDUnit"),
            near_pd=float(elem.get("nearPD")) if elem.get("nearPD") else None,
            near_pd_unit=elem.get("nearPDUnit"),
            base_curve=float(elem.get("baseCurve")) if elem.get("baseCurve") else None,
            base_curve_unit=elem.get("baseCurveUnit"),
            diameter=float(elem.get("diameter")) if elem.get("diameter") else None,
            diameter_unit=elem.get("diameterUnit"),
            vision_prescription_id=vision_prescription_id,
        )

    def _parse_vision_attachment(
        self, elem: Any, vision_prescription_id: int
    ) -> VisionAttachment:
        """Parse Attachment element."""
        return VisionAttachment(
            identifier=elem.get("identifier"),
            vision_prescription_id=vision_prescription_id,
        )

    def _parse_metadata_entry(
        self, elem: Any, parent_type: str, parent_id: int
    ) -> MetadataEntry:
        """Parse MetadataEntry element."""
        return MetadataEntry(
            key=elem.get("key"),
            value=elem.get("value"),
            parent_type=parent_type,
            parent_id=parent_id,
        )

    def _parse_hrv_list(self, record_id: int) -> HeartRateVariabilityMetadataList:
        """Parse HeartRateVariabilityMetadataList element."""
        return HeartRateVariabilityMetadataList(record_id=record_id)

    def _parse_instantaneous_bpm(
        self, elem: Any, hrv_list_id: int, base_date: datetime | None = None
    ) -> InstantaneousBeatsPerMinute:
        """Parse InstantaneousBeatsPerMinute element."""
        return InstantaneousBeatsPerMinute(
            bpm=int(elem.get("bpm")),
            time=self._parse_datetime(elem.get("time"), base_date),
            hrv_list_id=hrv_list_id,
        )


if __name__ == "__main__":
    # Example usage
    parser = AppleHealthParser()
    parser.parse_file("data/export.xml")
