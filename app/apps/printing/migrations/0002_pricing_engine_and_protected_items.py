# Generated for Panorama Backend v2 server-side pricing engine.
import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printing", "0001_initial"), ("accounts", "0004_security_rbac_and_email_otp")]
    operations = [
        migrations.CreateModel(
            name="PrintPickupLocation",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("name", models.CharField(max_length=150)), ("address", models.TextField(blank=True)),
                ("instructions", models.TextField(blank=True)), ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
            ], options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="PrintPricingRule",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("name", models.CharField(max_length=150)),
                ("color_mode", models.CharField(choices=[("black_white", "Black and White"), ("color", "Color")], max_length=32)),
                ("paper_size", models.CharField(choices=[("A4", "A4"), ("A5", "A5"), ("A3", "A3")], max_length=8)),
                ("sides", models.CharField(choices=[("one_sided", "One Sided"), ("double_sided", "Double Sided")], max_length=32)),
                ("price_per_sheet", models.DecimalField(decimal_places=2, max_digits=12)),
                ("setup_fee", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("currency", models.CharField(default="SYP", max_length=8)), ("is_active", models.BooleanField(default=True)),
                ("effective_from", models.DateTimeField(default=django.utils.timezone.now)),
                ("effective_to", models.DateTimeField(blank=True, null=True)),
            ], options={"ordering": ["-effective_from", "name"]},
        ),
        migrations.AddIndex(model_name="printpricingrule", index=models.Index(fields=["color_mode", "paper_size", "sides", "is_active"], name="printing_rule_lookup_idx")),
        migrations.AddIndex(model_name="printpricingrule", index=models.Index(fields=["effective_from", "effective_to"], name="printing_rule_effective_idx")),
        migrations.CreateModel(
            name="PrintBindingPrice",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("binding", models.CharField(choices=[("none", "None"), ("staple", "Staple"), ("spiral", "Spiral"), ("thermal", "Thermal")], max_length=32, unique=True)),
                ("price_per_copy", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("currency", models.CharField(default="SYP", max_length=8)), ("is_active", models.BooleanField(default=True)),
            ],
        ),
        migrations.AddField(model_name="printorder", name="currency", field=models.CharField(default="SYP", max_length=8)),
        migrations.AddField(model_name="printorder", name="pricing_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printorder", name="price_calculated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="printorder", name="pickup_location",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="orders", to="printing.printpickuplocation"),
        ),
        migrations.AlterField(model_name="printorder", name="total_price", field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AlterField(model_name="printorderitem", name="uploaded_file", field=models.FileField(blank=True, null=True, upload_to="print_orders/")),
        migrations.AddField(model_name="printorderitem", name="sheets_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="printorderitem", name="unit_price", field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField(model_name="printorderitem", name="binding_price", field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField(model_name="printorderitem", name="pricing_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="printorderitem", name="price", field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.CreateModel(
            name="PrintItemAccessTicket",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()), ("max_uses", models.PositiveSmallIntegerField(default=8)),
                ("use_count", models.PositiveSmallIntegerField(default=0)), ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_tickets", to="printing.printorderitem")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="print_item_access_tickets", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="printitemaccessticket", index=models.Index(fields=["token", "expires_at"], name="printing_item_ticket_idx")),
    ]
