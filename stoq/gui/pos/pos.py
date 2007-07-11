# -*- Mode: Python; coding: iso-8859-1 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005-2007 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   Evandro Vale Miquelito      <evandro@async.com.br>
##              Henrique Romano             <henrique@async.com.br>
##              Ariqueli Tejada Fonseca     <aritf@async.com.br>
##              Johan Dahlin                <jdahlin@async.com.br>
##
""" Main interface definition for pos application.  """

import gettext
from decimal import Decimal

import gtk
from kiwi.datatypes import currency, converter
from kiwi.argcheck import argcheck
from kiwi.log import Logger
from kiwi.ui.widgets.list import Column
from kiwi.python import Settable
from stoqdrivers.enum import UnitType
from stoqlib.database.runtime import (new_transaction, get_current_user,
                                      rollback_and_begin, finish_transaction)
from stoqlib.domain.interfaces import IDelivery, ISalesPerson, IPaymentGroup
from stoqlib.domain.devices import DeviceSettings
from stoqlib.domain.product import ProductSellableItem, ProductAdaptToSellable
from stoqlib.domain.person import PersonAdaptToClient
from stoqlib.domain.sellable import ASellable
from stoqlib.domain.service import ServiceSellableItem, Service
from stoqlib.domain.till import Till
from stoqlib.drivers.cheque import print_cheques_for_payment_group
from stoqlib.drivers.scale import read_scale_info
from stoqlib.exceptions import StoqlibError, TillError
from stoqlib.lib.message import info, warning, yesno
from stoqlib.lib.validators import format_quantity
from stoqlib.lib.parameters import sysparam
from stoqlib.gui.base.gtkadds import button_set_image_with_label
from stoqlib.gui.dialogs.clientdetails import ClientDetailsDialog
from stoqlib.gui.editors.personeditor import ClientEditor
from stoqlib.gui.editors.deliveryeditor import DeliveryEditor
from stoqlib.gui.editors.serviceeditor import ServiceItemEditor
from stoqlib.gui.fiscalprinter import FiscalPrinterHelper
from stoqlib.gui.search.giftcertificatesearch import GiftCertificateSearch
from stoqlib.gui.search.personsearch import ClientSearch
from stoqlib.gui.search.productsearch import ProductSearch
from stoqlib.gui.search.salesearch import SaleSearch
from stoqlib.gui.search.sellablesearch import SellableSearch
from stoqlib.gui.search.servicesearch import ServiceSearch
from stoqlib.gui.wizards.salewizard import ConfirmSaleWizard
from stoqlib.gui.wizards.personwizard import run_person_role_dialog
from stoqlib.reporting.sale import SaleOrderReport

from stoq.gui.application import AppWindow
from stoq.gui.pos.neworder import NewOrderEditor

_ = gettext.gettext
log = Logger('stoq.pos')

class POSApp(AppWindow):

    app_name = _('Point of Sales')
    app_icon_name = 'stoq-pos-app'
    gladefile = "pos"
    order_widgets =  ('client',
                      'order_number',
                      'salesperson')
    klist_name = 'sellables'

    def __init__(self, app):
        AppWindow.__init__(self, app)
        self.sale = None
        self.param = sysparam(self.conn)
        self.max_results = self.param.MAX_SEARCH_RESULTS
        self.client_table = PersonAdaptToClient
        self._product_table = ProductAdaptToSellable
        self._coupon = None
        self._printer = FiscalPrinterHelper(
            self.conn, parent=self.get_toplevel())
        self._scale_settings = DeviceSettings.get_scale_settings(self.conn)
        self._check_till()
        self._setup_widgets()
        self._setup_proxies()
        self._clear_order()
        self._update_widgets()

    def _set_product_on_sale(self):
        sellable = self._get_sellable()
        # If the sellable has a weight unit specified and we have a scale
        # configured for this station, go and check out what the printer says.
        if (sellable and sellable.unit and
            sellable.unit.unit_index == UnitType.WEIGHT and
            self._scale_settings):
            self._read_scale()

    def _setup_proxies(self):
        self.order_proxy = self.add_proxy(widgets=POSApp.order_widgets)
        model = Settable(quantity=Decimal(1), price=currency(0))
        self.sellableitem_proxy = self.add_proxy(model, ('quantity',))

    def _setup_widgets(self):
        if not self.param.HAS_DELIVERY_MODE:
            self.delivery_button.hide()
        if self.param.POS_FULL_SCREEN:
            self.get_toplevel().fullscreen()
        if self.param.POS_SEPARATE_CASHIER:
            for proxy in self.TillMenu.get_proxies():
                proxy.hide()
        if self.param.CONFIRM_SALES_ON_TILL:
            button_set_image_with_label(self.checkout_button,
                                        'confirm24px.png', _('Close'))

        self.order_total_label.set_size('xx-large')
        self.order_total_label.set_bold(True)

    def _update_totals(self):
        if self.sale:
            subtotal = self.sale.get_sale_subtotal()
        else:
            subtotal = currency(0)
        text = _(u"Total: %s") % converter.as_string(currency, subtotal)
        self.order_total_label.set_text(text)

    def _update_added_item(self, sellable_item, new_item=True):
        """Insert or update a klist item according with the new_item
        argument
        """
        if new_item:
            if self._coupon_add_item(sellable_item) == -1:
                return
            self.sellables.append(sellable_item)
        else:
            self.sellables.update(sellable_item)
        self.sellables.select(sellable_item)
        self.barcode.set_text('')
        self.barcode.grab_focus()
        self._reset_quantity_proxy()
        self._update_totals()

    @argcheck(ASellable, bool)
    def _update_list(self, sellable, notify_on_entry=False):
        quantity = self.sellableitem_proxy.model.quantity

        registered_price = self.sellableitem_proxy.model.price
        price = registered_price or sellable.price

        if isinstance(sellable.get_adapted(), Service) and quantity > 1:
            # It's not a common operation to add more than one item at
            # a time, it's also problematic since you'd have to show
            # one dialog per service item. See #3092
            info(_(u"Only one service was added since its not possible to"
                   "add more than one service to an order at a time."))

        sellable_item = sellable.add_sellable_item(self.sale,
                                                   quantity=quantity,
                                                   price=price)
        if (isinstance(sellable_item, ServiceSellableItem)
            and not self.run_dialog(ServiceItemEditor, self.conn,
                                    sellable_item)):
            return
        self._update_added_item(sellable_item)

    def _get_sellable(self):
        barcode = self.barcode.get_text()
        if not barcode:
            raise StoqlibError("_get_sellable needs a barcode")

        return ASellable.selectOneBy(barcode=barcode,
                                     status=ASellable.STATUS_AVAILABLE,
                                     connection=self.conn)

    def _select_first_item(self):
        if len(self.sellables):
            # XXX Probably kiwi should handle this for us. Waiting for
            # support
            self.sellables.select(self.sellables[0])

    def _update_widgets(self):
        try:
            has_till = Till.get_current(self.conn) is not None
            till_close = has_till
            till_open = not has_till
        except TillError:
            has_till = False
            till_close = True
            till_open = False

        self.TillOpen.set_sensitive(till_open and self.sale is None)
        self.TillClose.set_sensitive(till_close and self.sale is None)

        has_sellables = len(self.sellables) >= 1
        self.set_sensitive((self.checkout_button, self.remove_item_button,
                            self.PrintOrder, self.NewDelivery,
                            self.OrderCheckout), has_sellables)
        has_client = self.sale is not None and self.sale.client is not None
        self.EditClient.set_sensitive(has_client)
        self.ClientDetails.set_sensitive(has_client)
        self.delivery_button.set_sensitive(has_client and has_sellables)
        self.NewDelivery.set_sensitive(has_client and has_sellables)
        model = self.sellables.get_selected()
        self.edit_item_button.set_sensitive(
            model is not None and isinstance(model, ServiceSellableItem))
        self._update_totals()
        self._update_add_button()

    def _has_barcode_str(self):
        return self.barcode.get_text().strip() != ''

    def _update_add_button(self):
        has_barcode = self._has_barcode_str()
        self.add_button.set_sensitive(has_barcode)

    def _read_scale(self, sellable):
        data = read_scale_info(self.conn)
        self.quantity.set_value(data.weight)
        if self.param.USE_SCALE_PRICE:
            self.sellableitem_proxy.model.price = data.price_per_kg

    def _run_search_dialog(self, dialog_type, **kwargs):
        # Save the current state of order before calling a search dialog
        self.conn.commit()
        self.run_dialog(dialog_type, self.conn, **kwargs)

    def _run_advanced_search(self, search_str=None):
        # Ouch!, commit now ? yes, because SearchDialog synchronizes self.conn
        # internally. Actually this is not a problem since our sale object
        # is not valid (_is_valid_model defaults to False) until we finish the
        # whole process trough SaleWizard.
        self.conn.commit()
        sellable_view_item = self.run_dialog(SellableSearch, self.conn,
                                selection_mode=gtk.SELECTION_BROWSE,
                                search_str=search_str, order=self.sale,
                                quantity=self.sellableitem_proxy.model.quantity)
        if not sellable_view_item:
            return

        sellable = ASellable.get(sellable_view_item.id, connection=self.conn)
        self._update_list(sellable)
        self.barcode.grab_focus()

    def _reset_quantity_proxy(self):
        self.sellableitem_proxy.model.quantity = Decimal(1)
        self.sellableitem_proxy.update('quantity')
        self.sellableitem_proxy.model.price = None

    #
    # Sale Order operations
    #

    def _add_sellable_item(self, search_str=None):
        if not self.add_button.get_property('sensitive'):
            return
        sellable = self._get_sellable()
        self.add_button.set_sensitive(sellable is not None)
        if not sellable:
            self._run_advanced_search(search_str)
            return

        if isinstance(sellable, self._product_table):
            adapted = sellable.get_adapted()
            # If the sellable has a weight unit specified and we have a scale
            # configured for this station, go and check what the scale says.
            if (sellable and sellable.unit and
                sellable.unit.unit_index == UnitType.WEIGHT and
                self._scale_settings):
                self._read_scale(sellable)

        self._update_list(sellable, notify_on_entry=True)
        self.barcode.grab_focus()

    def _clear_order(self):
        log.info("Clearing order")
        self.sellables.clear()
        for widget in (self.search_box, self.list_vbox,
                       self.CancelOrder):
            widget.set_sensitive(False)
        self.sale = None

        self.order_proxy.set_model(None, relax_type=True)
        self._reset_quantity_proxy()
        self.barcode.set_text('')
        self.new_order_button.grab_focus()
        self._update_widgets()

    def _cancel_coupon(self):
        log.info("Cancelling coupon")
        if not self.param.CONFIRM_SALES_ON_TILL:
            if self._coupon:
                self._coupon.cancel()
        self._coupon = None

    def _delete_sellable_item(self, item):
        delivery = IDelivery(item, None)
        if delivery:
            for delivery_item in delivery.get_items():
                delivery.remove_item(delivery_item)
            table = type(delivery)
            table.delete(delivery.id, connection=self.conn)
        table = type(item)
        table.delete(item.id, connection=self.conn)
        self.sellables.remove(item)

    def _edit_sellable_item(self, item):
        if not isinstance(item, ServiceSellableItem):
            # Do not raise any exception here, since this method can be called
            # when the user activate a row with product in the sellables list.
            return
        if IDelivery(item, None):
            editor = DeliveryEditor
        else:
            editor = ServiceItemEditor
        model = self.run_dialog(editor, self.conn, item)
        if model:
            self.sellables.update(item)

    def _new_order(self):
        try:
            till = Till.get_current(self.conn)
            if till is None:
                warning(_(u"You need open the till before start doing sales."))
                return
        except TillError:
            if self._check_till():
                return

        if not ISalesPerson(get_current_user(self.conn).person, None):
            warning(_(u"You can't start a new sale, since you are not a "
                      "salesperson."))
            return

        if self._coupon is not None:
            if not self._cancel_order():
                return

        rollback_and_begin(self.conn)
        sale = self.run_dialog(NewOrderEditor, self.conn)
        if sale:
            log.info("Creating a new order")
            self.sellables.clear()
            self.order_proxy.set_model(sale)
            self.sale = sale
            self._update_widgets()
            self._update_totals()
            self.set_sensitive((self.search_box, self.footer_hbox,
                                self.list_vbox, self.CancelOrder),
                               True)
            self.barcode.grab_focus()
        else:
            rollback_and_begin(self.conn)
            return

    def _cancel_order(self):
        """
        Cancels the currently opened order.
        @returns: True if the order was canceled, otherwise false
        """
        if self.sale is not None:
            if yesno(_(u'The current order will be canceled, Confirm?'),
                         gtk.RESPONSE_NO,_(u"Go Back"), _(u"Cancel Order")):
                return False

        self._cancel_coupon()
        self._clear_order()

        return True

    def _add_delivery(self):
        if not self.sellables:
            warning(_("You don't have products to delivery."))
            return

        # FIXME: Use an SQL query
        products = [obj for obj in self.sellables
                        if isinstance(obj, ProductSellableItem)
                           and not obj.has_been_totally_delivered()]
        if not products:
            warning(_("All the products already have delivery "
                      "instructions"))
            return

        # We must commit now since we would like to have some of the
        # instances created in self.conn in a new transaction
        self.conn.commit()

        trans = new_transaction()
        sale = trans.get(self.sale)
        products = (trans.get(product) for product in products)
        service = self.run_dialog(DeliveryEditor, trans,
                                  sale=sale, products=products)
        rv = finish_transaction(trans, service)
        trans.close()
        if not rv:
            return

        # Synchronize self.conn and bring the new delivery instances to it
        self.conn.commit()
        service = type(service).get(service.id, connection=self.conn)
        self._update_added_item(service)

    def _check_till(self):
        if self._printer.needs_closing():
            if not self._close_till(can_remove_cash=False):
                return False
        return True

    def _open_till(self):
         if self._printer.open_till():
             self._update_widgets()

    def _close_till(self, can_remove_cash=True):
        retval = self._printer.close_till(can_remove_cash)
        if retval:
            self._update_widgets()
        return retval

    def _checkout(self):
        if self.param.CONFIRM_SALES_ON_TILL:
            self.sale.order()
        else:
            if not self._confirm_order():
                rollback_and_begin(self.conn)
                self._cancel_coupon()
                self._clear_order()
                return

        log.info("Checking out")
        self.conn.commit()
        self._clear_order()

    def _confirm_order(self):
        retval = self.run_dialog(ConfirmSaleWizard, self.conn, self.sale)
        if not retval:
            return False

        if self._coupon:
            if not self._coupon.totalize():
                return False
            if not self._coupon.setup_payments():
                return False
            if not self._coupon.close():
                return False

        self.sale.confirm()

        if self.sale.paid_with_money():
            self.sale.set_paid()

        print_cheques_for_payment_group(self.conn,
                                        IPaymentGroup(self.sale))

        return True

    #
    # Coupon related
    #

    def _open_coupon(self):
        self._coupon = self._printer.create_coupon(self.sale)
        if self.sale.client:
            self._coupon.identify_customer(self.sale.client.person)
        while not self._coupon.open():
            if not yesno(_(u"It is not possible to start a new sale if the "
                           "fiscal coupon cannot be opened."),
                         gtk.RESPONSE_YES, _(u"Try Again"), _(u"Cancel")):
                self.app.shutdown()
                break

    def _coupon_add_item(self, sellable_item):
        if self.param.CONFIRM_SALES_ON_TILL:
            return
        if self._coupon is None:
            self._open_coupon()
        return self._coupon.add_item(sellable_item)

    def _coupon_remove_item(self, sellable_item):
        if self.param.CONFIRM_SALES_ON_TILL:
            return

        self._coupon.remove_item(sellable_item)

    #
    # AppWindow Hooks
    #

    def setup_focus(self):
        # Setting up the widget groups
        self.main_vbox.set_focus_chain([self.order_details_hbox,
                                        self.pos_vbox])

        self.pos_vbox.set_focus_chain([self.list_header_hbox, self.list_vbox])
        self.list_vbox.set_focus_chain([self.footer_hbox])
        self.footer_hbox.set_focus_chain([self.toolbar_vbox])

        # Setting up the toolbar area
        self.toolbar_vbox.set_focus_chain([self.toolbar_button_box])
        self.toolbar_button_box.set_focus_chain([self.remove_item_button,
                                                 self.delivery_button,
                                                 self.checkout_button])

        # Setting up the barcode area
        self.list_header_hbox.set_focus_chain([self.search_box,
                                               self.stoq_logo])
        self.item_hbox.set_focus_chain([self.barcode, self.quantity,
                                        self.item_button_box])
        self.item_button_box.set_focus_chain([self.barcode, self.quantity,
                                              self.add_button,
                                              self.advanced_search])

    def get_columns(self):
        return [Column('sellable.code', title=_('Reference'), sorted=True,
                       data_type=int, width=95, justify=gtk.JUSTIFY_RIGHT,
                       format='%05d'),
                Column('sellable.base_sellable_info.description',
                       title=_('Description'), data_type=str, expand=True,
                       searchable=True),
                Column('price', title=_('Price'), data_type=currency,
                       width=110, justify=gtk.JUSTIFY_RIGHT),
                Column('quantity', title=_('Quantity'), data_type=Decimal,
                       width=110, format_func=format_quantity,
                       justify=gtk.JUSTIFY_RIGHT),
                Column('sellable.unit_description', title=_('Unit'),
                       data_type=str, width=70),
                Column('total', title=_('Total'), data_type=currency,
                       justify=gtk.JUSTIFY_RIGHT, width=100)]

    #
    # Actions
    #

    def on_EditClient__activate(self, action):
        if not (self.sale and self.sale.client):
            raise ValueError('You must have a client defined at this point')
        log.info("Editing client")
        if run_person_role_dialog(ClientEditor, self, self.conn,
                                  self.sale.client):
            self.conn.commit()

    def on_ClientDetails__activate(self, action):
        if not (self.sale and self.sale.client):
            raise ValueError("You should have a client defined at this point")
        self.run_dialog(ClientDetailsDialog, self.conn, self.sale.client)

    def on_CancelOrder__activate(self, action):
        self._cancel_order()

    def on_ResetOrder__activate(self, action):
        self._new_order()

    def on_Clients__activate(self, action):
        self._run_search_dialog(ClientSearch, hide_footer=True)

    def on_Sales__activate(self, action):
        self._run_search_dialog(SaleSearch)

    def on_ProductSearch__activate(self, action):
        self._run_search_dialog(ProductSearch, hide_footer=True,
                                hide_toolbar=True, hide_cost_column=True)

    def on_ServiceSearch__activate(self, action):
        self._run_search_dialog(ServiceSearch, hide_toolbar=True,
                                hide_cost_column=True)

    def on_GiftCertificateSearch__activate(self, action):
        self._run_search_dialog(GiftCertificateSearch, hide_footer=True,
                                hide_toolbar=True)

    def on_PrintOrder__activate(self, action):
        self.print_report(SaleOrderReport, self.sale)

    def on_OrderCheckout__activate(self, action):
        self._checkout()

    def on_NewDelivery__activate(self, action):
        self._add_delivery()

    def on_TillClose__activate(self, action):
        if not yesno(_(u"You can only close the till once per day. "
                       "\n\nClose the till?"),
                     gtk.RESPONSE_NO, _(u"Not now"), _("Close Till")):
            self._close_till()

    def on_TillOpen__activate(self, action):
         self._open_till()

    #
    # Other callbacks
    #

    def on_advanced_search__clicked(self, button):
        self._run_advanced_search()

    def on_add_button__clicked(self, button):
        self._add_sellable_item()

    def on_barcode__activate(self, entry):
        if not self._has_barcode_str():
            return
        search_str = self.barcode.get_text()
        self._add_sellable_item(search_str)

    def after_barcode__changed(self, editable):
        self._update_add_button()

    def on_quantity__activate(self, entry):
        self._add_sellable_item()

    def on_sellables__selection_changed(self, sellables, selected):
        self._update_widgets()

    def on_remove_item_button__clicked(self, button):
        sellable = self.sellables.get_selected()
        self._coupon_remove_item(sellable)
        self._delete_sellable_item(sellable)
        self._select_first_item()
        self._update_widgets()

    def on_delivery_button__clicked(self, button):
        self._add_delivery()

    def on_checkout_button__clicked(self, button):
        self._checkout()

    def on_new_order_button__clicked(self, button):
        self._new_order()

    def on_edit_item_button__clicked(self, button):
        item = self.sellables.get_selected()
        if item is None:
            raise StoqlibError("You should have a item selected "
                               "at this point")
        self._edit_sellable_item(item)

    def on_sellables__row_activated(self, sellables, sellable):
        self._edit_sellable_item(sellable)

