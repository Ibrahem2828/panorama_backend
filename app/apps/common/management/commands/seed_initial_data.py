import secrets

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.choices import StudentVerificationStatus, UserRole
from apps.accounts.models import StudentProfile, User
from apps.accounts.student_number import apply_student_number_parse
from apps.announcements.models import Announcement, AnnouncementTargetUserType
from apps.files.models import FileResource, FileVisibility
from apps.groups.models import Group, GroupMembership, GroupMembershipRole, GroupMembershipStatus
from apps.printing.models import PrintOrder, PrintOrderPriority
from apps.support.models import SupportTicket, SupportTicketCategory, SupportTicketMessage
from apps.universities.models import AcademicYear, Faculty, Major, Semester, Subject, University


class Command(BaseCommand):
    help = "Seed Panorama MVP initial and demo data."

    def handle(self, *args, **options):
        self.stdout.write("Seeding Panorama MVP data...")
        university, _ = University.objects.update_or_create(
            code="SPU",
            defaults={"name": "الجامعة السورية الخاصة", "description": "Syrian Private University", "is_active": True},
        )

        faculties_data = [
            ("1", "كلية الطب البشري", "Human Medicine", "الطب البشري"),
            ("2", "كلية طب الأسنان", "Dentistry", "طب الأسنان"),
            ("3", "كلية الصيدلة", "Pharmacy", "الصيدلة"),
            ("4", "كلية هندسة المعلوماتية", "Informatics Engineering", "هندسة المعلوماتية"),
            ("5", "كلية هندسة البترول", "Petroleum Engineering", "هندسة البترول"),
            ("6", "كلية إدارة الأعمال", "Business Administration", "إدارة الأعمال"),
            (
                "7",
                "كلية هندسة تكنولوجيا البناء والتشييد",
                "Construction Technology Engineering",
                "هندسة تكنولوجيا البناء والتشييد",
            ),
        ]
        faculties = {}
        majors = {}
        for code, arabic_name, _english_name, major_name in faculties_data:
            faculty, _ = Faculty.objects.update_or_create(
                university=university,
                code=code,
                defaults={"name": arabic_name, "is_active": True},
            )
            major, _ = Major.objects.update_or_create(
                faculty=faculty,
                code=code,
                defaults={"name": major_name, "is_active": True},
            )
            faculties[code] = faculty
            majors[code] = major

        years = {}
        for order in range(1, 7):
            year, _ = AcademicYear.objects.update_or_create(
                order=order,
                defaults={
                    "name": f"السنة {['الأولى', 'الثانية', 'الثالثة', 'الرابعة', 'الخامسة', 'السادسة'][order - 1]}",
                    "is_active": True,
                },
            )
            years[order] = year

        semesters = {}
        for order, name in [(1, "الفصل الأول"), (2, "الفصل الثاني")]:
            semester, _ = Semester.objects.update_or_create(order=order, defaults={"name": name, "is_active": True})
            semesters[order] = semester

        subjects = {}
        samples = {
            "1": ["تشريح 1", "فيزيولوجيا 1"],
            "2": ["تشريح 1", "مواد سنية 1"],
            "3": ["كيمياء صيدلانية", "علم الأدوية 1"],
            "4": ["برمجة 1", "رياضيات 1"],
            "5": ["جيولوجيا النفط", "رياضيات هندسية"],
            "6": ["مبادئ الإدارة", "محاسبة 1"],
            "7": ["مواد بناء", "رسم هندسي"],
        }
        for code, names in samples.items():
            for idx, subject_name in enumerate(names, start=1):
                subject, _ = Subject.objects.update_or_create(
                    major=majors[code],
                    academic_year=years[1],
                    semester=semesters[1],
                    code=f"{code}{idx}",
                    defaults={"name": subject_name, "description": "Demo subject", "is_active": True},
                )
                subjects[(code, idx)] = subject

        password = secrets.token_urlsafe(32)
        users = {
            "it": self._user(
                "it@panorama.local",
                "+963900000001",
                "IT Support",
                UserRole.IT_SUPPORT,
                password,
                is_staff=True,
                is_superuser=True,
            ),
            "admin": self._user("admin@panorama.local", "+963900000002", "Admin User", UserRole.ADMIN, password),
            "print": self._user("print@panorama.local", "+963900000003", "Print Staff", UserRole.PRINT_STAFF, password),
            "student": self._user(
                "student@panorama.local",
                "+963900000004",
                "Demo Student",
                UserRole.STUDENT,
                password,
                is_phone_verified=True,
            ),
            "normal": self._user("user@panorama.local", "+963900000005", "Normal User", UserRole.NORMAL_USER, password),
        }

        profile, _ = StudentProfile.objects.get_or_create(user=users["student"])
        profile.university = university
        profile.faculty = faculties["2"]
        profile.major = majors["2"]
        profile.academic_year = years[1]
        profile.semester = semesters[1]
        profile.student_number = "2150094"
        profile.verification_status = StudentVerificationStatus.APPROVED
        profile.verified_at = profile.verified_at or timezone.now()
        apply_student_number_parse(profile, profile.student_number, auto_link_faculty=False)
        profile.save()

        dentistry_year_group, _ = Group.objects.update_or_create(
            name="طب الأسنان - السنة الأولى",
            defaults={
                "description": "Demo first-year dentistry group",
                "university": university,
                "faculty": faculties["2"],
                "major": majors["2"],
                "academic_year": years[1],
                "created_by": users["admin"],
                "requires_approval": True,
                "send_messages_permission": "all_members",
                "is_active": True,
            },
        )
        dentistry_subject_group, _ = Group.objects.update_or_create(
            name="طب الأسنان - تشريح 1",
            defaults={
                "description": "Subject-specific dentistry group",
                "university": university,
                "faculty": faculties["2"],
                "major": majors["2"],
                "academic_year": years[1],
                "semester": semesters[1],
                "subject": subjects[("2", 1)],
                "created_by": users["admin"],
                "requires_approval": True,
                "send_messages_permission": "admins_only",
                "is_active": True,
            },
        )
        Group.objects.update_or_create(
            name="هندسة المعلوماتية - برمجة 1",
            defaults={
                "description": "Programming demo group",
                "university": university,
                "faculty": faculties["4"],
                "major": majors["4"],
                "academic_year": years[1],
                "semester": semesters[1],
                "subject": subjects[("4", 1)],
                "created_by": users["admin"],
                "requires_approval": True,
                "send_messages_permission": "all_members",
                "is_active": True,
            },
        )

        GroupMembership.objects.update_or_create(
            group=dentistry_year_group,
            user=users["student"],
            defaults={
                "status": GroupMembershipStatus.APPROVED,
                "role": GroupMembershipRole.MEMBER,
                "joined_at": timezone.now(),
            },
        )
        GroupMembership.objects.update_or_create(
            group=dentistry_subject_group,
            user=users["student"],
            defaults={
                "status": GroupMembershipStatus.APPROVED,
                "role": GroupMembershipRole.MODERATOR,
                "joined_at": timezone.now(),
            },
        )

        self._file("Public Welcome File", users["admin"], FileVisibility.PUBLIC)
        self._file("Verified Student Guide", users["admin"], FileVisibility.VERIFIED_STUDENTS_ONLY)
        self._file(
            "Dentistry Major Notes",
            users["admin"],
            FileVisibility.MAJOR_ONLY,
            major=majors["2"],
            academic_year=years[1],
        )
        self._file("Dentistry Group File", users["admin"], FileVisibility.GROUP_ONLY, group=dentistry_year_group)

        Announcement.objects.update_or_create(
            title="Welcome to Panorama",
            defaults={"description": "General demo announcement", "created_by": users["admin"]},
        )
        Announcement.objects.update_or_create(
            title="Verified Students Update",
            defaults={
                "description": "Announcement for verified students",
                "target_user_type": AnnouncementTargetUserType.VERIFIED_STUDENTS,
                "created_by": users["admin"],
            },
        )
        Announcement.objects.update_or_create(
            title="Dentistry Notice",
            defaults={
                "description": "Dentistry-targeted announcement",
                "target_user_type": AnnouncementTargetUserType.VERIFIED_STUDENTS,
                "target_major": majors["2"],
                "created_by": users["admin"],
            },
        )
        Announcement.objects.update_or_create(
            title="Printing Service Available",
            defaults={"description": "Printing service announcement", "created_by": users["admin"]},
        )

        PrintOrder.objects.get_or_create(
            user=users["student"],
            user_notes="Demo student order",
            defaults={"priority": PrintOrderPriority.STUDENT_PRIORITY},
        )
        PrintOrder.objects.get_or_create(
            user=users["normal"], user_notes="Demo normal order", defaults={"priority": PrintOrderPriority.NORMAL}
        )

        ticket, _ = SupportTicket.objects.get_or_create(
            user=users["student"], subject="Demo support ticket", defaults={"category": SupportTicketCategory.TECHNICAL}
        )
        SupportTicketMessage.objects.get_or_create(
            ticket=ticket, sender=users["student"], message="I need help with the demo app."
        )

        self.stdout.write(self.style.SUCCESS("Seed data ready. Demo accounts use generated, non-disclosed passwords."))

    def _user(
        self, email, phone, full_name, role, password, is_staff=False, is_superuser=False, is_phone_verified=False
    ):
        user, _ = User.objects.update_or_create(
            email=email,
            defaults={
                "phone_number": phone,
                "full_name": full_name,
                "role": role,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
                "is_active": True,
                "is_phone_verified": is_phone_verified,
            },
        )
        user.set_password(password)
        user.save()
        return user

    def _file(self, title, uploaded_by, visibility, major=None, academic_year=None, group=None):
        file_resource, created = FileResource.objects.get_or_create(
            title=title,
            defaults={
                "description": "Seeded demo file",
                "uploaded_by": uploaded_by,
                "visibility": visibility,
                "major": major,
                "academic_year": academic_year,
                "group": group,
                "is_active": True,
            },
        )
        if created or not file_resource.file:
            file_resource.file.save(
                f"{title.lower().replace(' ', '_')}.txt", ContentFile(b"Panorama demo file"), save=True
            )
        return file_resource
