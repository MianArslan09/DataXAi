from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from warehouse.models import Customer, OrderLine, Product


@pytest.mark.django_db
class TestWarehouseModels:
    def test_create_customer_product_orderline(self):
        customer = Customer.objects.create(external_customer_id="17850", country="United Kingdom")
        product = Product.objects.create(
            stock_code="85123A", description="WHITE HANGING HEART T-LIGHT HOLDER"
        )
        line = OrderLine.objects.create(
            invoice_no="536365",
            product=product,
            customer=customer,
            quantity=6,
            unit_price=Decimal("2.55"),
            invoice_date="2010-12-01T08:26:00Z",
        )
        assert line.pk is not None
        assert line.is_cancellation is False
        assert customer.order_lines.count() == 1

    def test_negative_quantity_is_allowed_for_returns(self):
        """Negative quantity = a return/cancellation, a legitimate business
        record, not a data quality fault - see Volume 3 model docstring."""
        product = Product.objects.create(stock_code="22423", description="REGENCY CAKESTAND 3 TIER")
        line = OrderLine.objects.create(
            invoice_no="C536379",
            product=product,
            customer=None,
            quantity=-1,
            unit_price=Decimal("12.75"),
            invoice_date="2010-12-01T09:41:00Z",
            is_cancellation=True,
        )
        assert line.quantity == -1

    def test_negative_unit_price_is_rejected(self):
        """Unlike quantity, a negative price is a genuine fault (Table 1:
        Range Violations) - the DB constraint should reject it outright."""
        product = Product.objects.create(stock_code="99999", description="TEST SKU")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                OrderLine.objects.create(
                    invoice_no="536999",
                    product=product,
                    quantity=1,
                    unit_price=Decimal("-5.00"),
                    invoice_date="2010-12-01T10:00:00Z",
                )

    def test_customer_external_id_must_be_unique(self):
        Customer.objects.create(external_customer_id="12345", country="Pakistan")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(external_customer_id="12345", country="Pakistan")
