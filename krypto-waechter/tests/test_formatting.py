import unittest

import helpers  # noqa: F401  (setzt den Suchpfad)

from krypto_waechter.formatting import (format_amount, format_change, format_money, format_number, format_percent,
                                        format_plain, format_price, parse_number, price_decimals, window_label)


class FormattingTest(unittest.TestCase):
    def test_german_numbers(self):
        self.assertEqual(format_number(1234567.891, 2), "1.234.567,89")
        self.assertEqual(format_number(-0.001, 2), "0,00")
        self.assertEqual(format_number(-12.5, 1), "-12,5")

    def test_prices_use_sensible_decimals(self):
        self.assertEqual(format_price(57123.456, "eur"), "57.123,46 €")
        self.assertEqual(format_price(2.12345, "EUR"), "2,1235 €")
        self.assertEqual(format_price(0.35123, "EUR"), "0,3512 €")
        self.assertEqual(format_price(0.00001234, "EUR"), "0,00001234 €")
        self.assertEqual(format_price(1.5, "USDT"), "1,5000 USDT")
        self.assertEqual(format_price(None, "EUR"), "–")
        self.assertEqual(price_decimals(0), 2)

    def test_money_and_percent(self):
        self.assertEqual(format_money(1234.5, "EUR", signed=True), "+1.234,50 €")
        self.assertEqual(format_money(-3, "USD", signed=True), "-3,00 $")
        self.assertEqual(format_money(0.001, "EUR", signed=True), "0,00 €")
        self.assertEqual(format_percent(2.345), "+2,35 %")
        self.assertEqual(format_percent(-7.8), "-7,80 %")
        self.assertEqual(format_percent(None), "–")
        self.assertEqual(format_change(3.1), "▲ +3,10 %")
        self.assertEqual(format_change(-0.5), "▼ -0,50 %")
        self.assertEqual(format_change(None), "–")

    def test_amounts_and_plain_values(self):
        self.assertEqual(format_amount(0.05), "0,05")
        self.assertEqual(format_amount(1250), "1.250")
        self.assertEqual(format_amount(0.00012345), "0,00012345")
        self.assertEqual(format_plain(2.5), "2,5")
        self.assertEqual(format_plain(10), "10")

    def test_window_labels(self):
        self.assertEqual(window_label(5), "5 Min")
        self.assertEqual(window_label(60), "1 Std")
        self.assertEqual(window_label(240), "4 Std")
        self.assertEqual(window_label(1440), "24 Std")
        self.assertEqual(window_label(10080), "7 Tage")
        self.assertEqual(window_label(90), "90 Min")

    def test_parse_number_like_germans_type_it(self):
        cases = {
            "1.234,56": 1234.56, "0,5": 0.5, "58.000": 58000, "1.250.000": 1250000, "0.123": 0.123,
            "2.5": 2.5, "1,234.56": 1234.56, "45000 €": 45000, " +3 % ": 3, "12.3456": 12.3456,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_number(text), expected)
        self.assertIsNone(parse_number("  "))
        for bad in ("abc", "1.2.3", "1,2,3", "nan", "inf"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_number(bad)

    def test_formatted_values_can_be_read_back(self):
        # So werden gespeicherte Werte im Bearbeiten-Dialog vorbelegt – sie dürfen sich nicht verändern
        for value in (58000.0, 0.05, 1250.0, 0.00001234, 1234.5678, 2.5, 0.1 + 0.2):
            with self.subTest(value=value):
                self.assertAlmostEqual(parse_number(format_amount(value)), value)
                self.assertAlmostEqual(parse_number(format_plain(value, max_decimals=10)), value, places=9)


if __name__ == "__main__":
    unittest.main()
