# Copyright 2022 Tecnativa - David Vidal
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import Command, _, fields, models
from odoo.tools import plaintext2html


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    sale_order_id = fields.Many2one(comodel_name="sale.order")

    def _prepare_quotation(self):
        return {
            "partner_id": self.partner_id.id or self.create_uid.partner_id.id,
            "origin": self.survey_id.title,
            "survey_user_input_id": self.id,
            "company_id": self.create_uid.company_id.id,
            "team_id": self.survey_id.crm_team_id.id,
        }

    def _prepare_quotation_line(self, input_line, product):
        if input_line.question_id.question_type == "numerical_box":
            qty = input_line.value_numerical_box
        else:
            # We can set a related question that will be the qty multiplier
            qty_question = self.user_input_line_ids.filtered(
                lambda x: x.question_id
                == input_line.question_id.product_uom_qty_question_id
            )
            # We'll accept 0 as a valid answer. Survey admin can deal with answer
            # validation on top of it
            qty = qty_question.value_numerical_box if qty_question else 1
        return {
            "product_id": product.id,
            "product_uom_qty": qty,
        }

    def _prepare_quotation_comment(self):
        """We can have surveys without partner. It's handy to have some relevant info
        in the initial internal message for the salesmen to complete.

        :return str: comment for the quotation internal message
        """
        relevant_answers = self.user_input_line_ids.filtered(
            lambda x: not x.skipped and x.question_id.show_in_sale_order_comment
        )
        comment = "\n".join(
            f"{answer.question_id.title}: {answer[f'value_{answer.answer_type}']}"
            for answer in relevant_answers
        )
        return comment

    def _create_quotation_post_process(self):
        """After creating the quotation send an internal message with practical info"""
        sale_sudo = self.sale_order_id.sudo()
        message = _(
            "This order has been created from this survey input: "
            "<a href=# data-oe-model=survey.user_input data-oe-id=%(id)d>%(title)s</a>"
        ) % {"id": self.id, "title": self.survey_id.title}
        additional_comment = self._prepare_quotation_comment()
        if additional_comment:
            message += (
                f"<p>{_('Relevant answer informations:')}</p>"
                f"<p>{plaintext2html(additional_comment)}</p>"
            )
        sale_sudo.message_post(body=message)
        if self.survey_id.send_quotation_to_customer:
            email_act = sale_sudo.action_quotation_send()
            email_ctx = email_act.get("context", {})
            template = self.survey_id.quotation_mail_template_id.id or email_ctx.get(
                "default_template_id"
            )
            sale_sudo.with_context(**email_ctx).message_post_with_template(template)

    def _mark_done(self):
        """Generate the sale order when the survey is submitted"""
        res = super()._mark_done()
        if not self.survey_id.generate_quotations:
            return res
        quotable_lines = self.user_input_line_ids.filtered(
            "suggested_answer_id.product_ids"
        )
        quotable_lines += self.user_input_line_ids.filtered(
            lambda x: x.question_id.product_ids and not x.skipped
        )
        if not quotable_lines:
            return res
        vals = self._prepare_quotation()
        quotable_lines_pairs = []
        # We can set multiple products, so for each one a sale line is created
        for input_line in quotable_lines:
            if input_line.question_id.question_type == "numerical_box":
                product_ids = input_line.question_id.product_ids
            else:
                product_ids = input_line.suggested_answer_id.product_ids
            if not product_ids:
                continue
            quotable_lines_pairs += [(input_line, product) for product in product_ids]
        vals["order_line"] = [
            Command.create(self._prepare_quotation_line(line, product))
            for line, product in quotable_lines_pairs
        ]
        self.sale_order_id = self.env["sale.order"].sudo().create(vals)
        self._create_quotation_post_process()
        return res
