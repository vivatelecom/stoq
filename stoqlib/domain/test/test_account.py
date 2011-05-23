# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2011 Async Open Source <http://www.async.com.br>
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
## Author(s): Stoq Team <stoq-devel@async.com.br>
##

from stoqlib.domain.account import Account, AccountTransaction
from stoqlib.domain.test.domaintest import DomainTest
from stoqlib.domain.interfaces import IDescribable
from stoqlib.lib.parameters import sysparam


class TestAccount(DomainTest):

    def testAccount(self):
        account = self.create_account()
        self.failUnless(account)

    def testAccountGetByStation(self):
        station = self.create_station()
        account = Account.get_by_station(self.trans, station)
        self.failIf(account)
        account = self.create_account()
        account.station = station

        account = Account.get_by_station(self.trans, station)
        self.failUnless(account)

        self.assertRaises(TypeError, Account.get_by_station,
                          self.trans, None)
        self.assertRaises(TypeError, Account.get_by_station,
                          self.trans, object())

    def testAccountCreateForStation(self):
        station = self.create_station()
        account = Account.create_for_station(self.trans, station)
        self.failUnless(account)
        self.assertEquals(account.station, station)
        self.assertEquals(account.parent, sysparam(self.trans).TILLS_ACCOUNT)

        self.assertRaises(ValueError, Account.create_for_station,
                          self.trans, station)

        self.assertRaises(TypeError, Account.create_for_station,
                          self.trans, None)
        self.assertRaises(TypeError, Account.create_for_station,
                          self.trans, object())

    def testAccountLongDescription(self):
        a1 = self.create_account()
        a1.description = "first"
        a2 = self.create_account()
        a2.description = "second"
        a2.parent = a1
        a3 = self.create_account()
        a3.description = "third"
        a3.parent = a2

        self.assertEquals(a1.long_description, 'first')
        self.assertEquals(a2.long_description, 'first:second')
        self.assertEquals(a3.long_description, 'first:second:third')

    def testAccountTransactions(self):
        account = self.create_account()
        self.failIf(account.transactions)

        transaction = self.create_account_transaction(account)

        self.failUnless(account.transactions)
        self.failUnless(transaction in account.transactions)

        a2 = self.create_account()
        t2 = self.create_account_transaction(a2)

        self.failIf(t2 in account.transactions)

        t2.source_account = account
        t2.sync()

        self.failUnless(t2 in account.transactions)

    def testAccountCanRemove(self):
        account = self.create_account()
        self.failUnless(account.can_remove())

        self.failIf(sysparam(self.trans).TILLS_ACCOUNT.can_remove())
        self.failIf(sysparam(self.trans).IMBALANCE_ACCOUNT.can_remove())
        self.failIf(sysparam(self.trans).BANKS_ACCOUNT.can_remove())

        station = self.create_station()
        account.station = station

        self.failIf(account.can_remove())

        account.station = None

        self.failUnless(account.can_remove())

        a2 = self.create_account()

        self.failUnless(account.can_remove())

        a2.parent = account

        self.failIf(account.can_remove())

    def testAccountRemove(self):
        a1 = self.create_account()
        a2 = self.create_account()

        imbalance_account = sysparam(self.trans).IMBALANCE_ACCOUNT

        t1 = self.create_account_transaction(a1)
        t1.source_account = a2
        t1.sync()

        t2 = self.create_account_transaction(a2)
        t2.source_account = a1
        t2.sync()

        a1.station = self.create_station()
        self.assertRaises(TypeError, a1.remove)
        a1.station = None

        a1.remove(self.trans)

        self.assertEquals(t1.account, imbalance_account)
        self.assertEquals(t2.source_account, imbalance_account)

    def has_child_accounts(self):
        a1 = self.create_account()
        a2 = self.create_account()

        self.failIf(a1.has_child_accounts)
        self.failIf(a2.has_child_accounts)

        a2.parent = a1

        self.failUnless(a1.has_child_accounts)
        self.failIf(a2.has_child_accounts)

    def testIDescribable(self):
        a1 = self.create_account()
        self.assertEquals(a1.long_description, IDescribable(a1).get_description())


class TestAccountTransaction(DomainTest):

    def testGetOtherAccount(self):
        a1 = self.create_account()
        a2 = self.create_account()

        t1 = self.create_account_transaction(a1)
        t2 = self.create_account_transaction(a2)

        t1.source_account = a2
        t2.source_account = a1

        t1.sync()
        t2.sync()

        self.assertEquals(t1.get_other_account(a1), a2)
        self.assertEquals(t1.get_other_account(a2), a1)

        self.assertEquals(t2.get_other_account(a1), a2)
        self.assertEquals(t2.get_other_account(a2), a1)

    def testSetOtherAccount(self):
        a1 = self.create_account()
        a2 = self.create_account()

        t1 = self.create_account_transaction(a1)
        t1.source_account = a2
        t1.sync()

        t2 = self.create_account_transaction(a2)
        t2.source_account = a1
        t2.sync()

        t1.set_other_account(a1, a2)
        t1.sync()
        self.assertEquals(t1.account, a1)
        self.assertEquals(t1.source_account, a2)
        t1.set_other_account(a2, a2)
        t1.sync()
        self.assertEquals(t1.account, a2)
        self.assertEquals(t1.source_account, a2)

        t2.set_other_account(a1, a2)
        t2.sync()
        self.assertEquals(t2.account, a2)
        self.assertEquals(t2.source_account, a1)
        t2.set_other_account(a2, a2)
        t2.sync()
        self.assertEquals(t2.account, a2)
        self.assertEquals(t2.source_account, a2)

    def testCreateFromPayment(self):
        sale = self.create_sale()
        payment = self.add_payments(sale).payment
        account = self.create_account()
        payment.method.destination_account = account
        transaction = AccountTransaction.create_from_payment(payment)
        imbalance_account = sysparam(self.trans).IMBALANCE_ACCOUNT
        self.assertEquals(transaction.source_account, imbalance_account)
        self.assertEquals(transaction.account, account)
        self.assertEquals(transaction.payment, payment)
