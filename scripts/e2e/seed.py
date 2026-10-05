# Seed deterministic accounts for the local full-stack run. Execute with:
#   DJANGO_SETTINGS_MODULE=config.settings.e2e python manage.py shell -c "exec(open('../scripts/e2e/seed.py').read())"
from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.universities.models import AcademicYear, Major, Semester

PASSWORD = "E2eStrongPass#2026"


def upsert(email, phone, name, role, **extra):
    user, created = User.objects.get_or_create(
        email=email, defaults={"phone_number": phone, "full_name": name, "role": role, **extra}
    )
    user.phone_number, user.full_name, user.role = phone, name, role
    user.is_active, user.is_email_verified, user.is_phone_verified = True, True, True
    for key, value in extra.items():
        setattr(user, key, value)
    user.set_password(PASSWORD)
    user.save()
    return user


upsert("it@e2e.test", "+963990000001", "E2E IT Support", UserRole.IT_SUPPORT, is_staff=True, is_superuser=True)
upsert("admin@e2e.test", "+963990000002", "E2E Admin", UserRole.ADMIN, is_staff=True)
upsert("print@e2e.test", "+963990000003", "E2E Print Staff", UserRole.PRINT_STAFF)
upsert("normal@e2e.test", "+963990000004", "E2E Normal User", UserRole.NORMAL_USER)
student = upsert("student@e2e.test", "+963990000005", "E2E Student", UserRole.STUDENT)

major = Major.objects.select_related("faculty", "faculty__university").first()
year, semester = AcademicYear.objects.first(), Semester.objects.first()
if major and year and semester:
    from apps.accounts.student_number import apply_student_number_parse

    profile, _ = StudentProfile.objects.update_or_create(
        user=student,
        defaults={
            "university": major.faculty.university,
            "faculty": major.faculty,
            "major": major,
            "academic_year": year,
            "semester": semester,
            "verification_status": StudentVerificationStatus.APPROVED,
            "student_number": "2150094",
        },
    )
    apply_student_number_parse(profile, "2150094", auto_link_faculty=False)
    profile.save()
print("seeded:", User.objects.count(), "users; academic ready:", bool(major and year and semester))
