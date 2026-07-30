from dataclasses import dataclass

from django.core.exceptions import ValidationError

FACULTY_CODE_LABELS = {
    "1": "HUMAN_MEDICINE",
    "2": "DENTISTRY",
    "3": "PHARMACY",
    "4": "INFORMATICS_ENGINEERING",
    "5": "PETROLEUM_ENGINEERING",
    "6": "BUSINESS_ADMINISTRATION",
    "7": "CONSTRUCTION_TECHNOLOGY_ENGINEERING",
}


@dataclass(frozen=True)
class ParsedStudentNumber:
    faculty_code: str
    faculty_label: str
    enrollment_year_code: str
    enrollment_year_full: int
    serial_number: str

    def as_dict(self) -> dict:
        return {
            "faculty_code": self.faculty_code,
            "faculty_label": self.faculty_label,
            "enrollment_year_code": self.enrollment_year_code,
            "enrollment_year_full": self.enrollment_year_full,
            "serial_number": self.serial_number,
        }


class StudentNumberParser:
    @staticmethod
    def parse(student_number: str) -> dict:
        raw = str(student_number or "").strip()
        if not raw.isdigit():
            raise ValidationError("Student number must contain digits only.")
        if len(raw) < 7:
            raise ValidationError("Student number must be at least 7 digits.")

        faculty_code = raw[0]
        if faculty_code not in FACULTY_CODE_LABELS:
            raise ValidationError("Student number contains an invalid faculty code.")

        enrollment_year_code = raw[1:3]
        serial_number = raw[3:]
        if not enrollment_year_code.isdigit() or len(enrollment_year_code) != 2:
            raise ValidationError("Enrollment year code must be two digits.")
        if not serial_number.isdigit():
            raise ValidationError("Student serial number must be numeric.")

        enrollment_year_full = 2000 + int(enrollment_year_code)
        return ParsedStudentNumber(
            faculty_code=faculty_code,
            faculty_label=FACULTY_CODE_LABELS[faculty_code],
            enrollment_year_code=enrollment_year_code,
            enrollment_year_full=enrollment_year_full,
            serial_number=serial_number,
        ).as_dict()

    @classmethod
    def validate(cls, student_number: str) -> None:
        cls.parse(student_number)

    @classmethod
    def get_faculty_code(cls, student_number: str) -> str:
        return cls.parse(student_number)["faculty_code"]

    @classmethod
    def get_enrollment_year_code(cls, student_number: str) -> str:
        return cls.parse(student_number)["enrollment_year_code"]

    @classmethod
    def get_serial_number(cls, student_number: str) -> str:
        return cls.parse(student_number)["serial_number"]

    @classmethod
    def resolve_faculty_name_or_code(cls, student_number: str) -> str:
        parsed = cls.parse(student_number)
        return parsed["faculty_label"]


def apply_student_number_parse(profile, student_number: str, *, auto_link_faculty: bool = True) -> dict:
    from apps.universities.models import Faculty

    parsed = StudentNumberParser.parse(student_number)
    profile.faculty_code_from_student_number = parsed["faculty_code"]
    profile.enrollment_year_code = parsed["enrollment_year_code"]
    profile.enrollment_year_full = parsed["enrollment_year_full"]
    profile.student_serial_number = parsed["serial_number"]
    if auto_link_faculty:
        faculty = Faculty.objects.filter(code=parsed["faculty_code"], is_deleted=False).first()
        if faculty and not profile.faculty_id:
            profile.faculty = faculty
            profile.university = faculty.university
            majors = list(faculty.majors.filter(is_deleted=False, is_active=True)[:2])
            if len(majors) == 1 and not profile.major_id:
                profile.major = majors[0]
    return parsed
