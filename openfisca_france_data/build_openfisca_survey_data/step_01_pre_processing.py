#! /usr/bin/env python
# -*- coding: utf-8 -*-


# OpenFisca -- A versatile microsimulation software
# By: OpenFisca Team <contact@openfisca.fr>
#
# Copyright (C) 2011, 2012, 2013, 2014 OpenFisca Team
# https://github.com/openfisca
#
# This file is part of OpenFisca.
#
# OpenFisca is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# OpenFisca is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import gc

import logging
from numpy import where, nan


from openfisca_france_data.surveys import SurveyCollection
from openfisca_france_data.build_openfisca_survey_data import save_temp, load_temp

log = logging.getLogger(__name__)

# Prepare the some useful merged tables

# Menages et Individus


def create_indivim(year = 2006):
    '''
    '''
    # load
    erfs_survey_collection = SurveyCollection.load(collection='erfs')
    survey = erfs_survey_collection.surveys['erfs_{}'.format(year)]

    erfmen = survey.get_values(table="erf_menage")
    eecmen = survey.get_values(table="eec_menage")
    log.info(erfmen.info())

    erfind = survey.get_values(table = "erf_indivi")
    eecind = survey.get_values(table = "eec_indivi")

    log.info(eecind.info())
    log.info(erfind.info())

    # travail sur la cohérence entre les bases
    noappar_m = eecmen[ ~(eecmen.ident.isin(erfmen.ident.values))]
    print 'describe noappar_m'
    print noappar_m.describe()

    noappar_i = eecmen[ ~(eecmen.ident.isin(erfmen.ident.values))]
    noappar_i = noappar_i.drop_duplicates(cols = 'ident', take_last = True)
    #TODO: vérifier qu'il n'y a théoriquement pas de doublon

    dif = set(noappar_i.ident).symmetric_difference(noappar_m.ident)
    int = set(noappar_i.ident) & set(noappar_m.ident)
    print "dif, int --------------------------------"
    print dif, int
    del noappar_i, noappar_m, dif, int
    gc.collect()

    #fusion enquete emploi et source fiscale
    menagem = erfmen.merge(eecmen)
    indivim = eecind.merge(erfind, on = ['noindiv', 'ident', 'noi'], how = "inner")

    # optimisation des types? Controle de l'existence en passant
    #TODO: minimal dtype
    # TODO: this should be done somewhere else
    var_list = (['acteu', 'stc', 'contra', 'titc', 'forter', 'mrec', 'rstg', 'retrai', 'lien', 'noicon',
                 'noiper', 'noimer', 'naia', 'cohab', 'agepr', 'statut', 'txtppb', 'encadr', 'prosa'])
    for var in var_list:
        try:
            indivim[var] = indivim[var].astype("float32")
        except:
            print "%s is missing" %var

    # création de variables
    ## actrec
    indivim['actrec'] = 0
    #TODO: pas de 6 ?!!
    filter1 = (indivim['acteu'] == 1) & (indivim['stc'].isin([1,3]))
    indivim['actrec'][filter1] = 1
    filter2 = (indivim['acteu'] == 1) & (((indivim['stc'] == 2) & (indivim['contra'] == 1)) | (indivim['titc'] == 2))
    indivim['actrec'][filter2] = 2
    indivim['actrec'][indivim['acteu'] == 1] =  3
    filter4 = (indivim['acteu'] == 2) | ((indivim['acteu'] == 3) & (indivim['mrec'] == 1))
    indivim['actrec'][filter4] = 4
    filter5 = (indivim['acteu'] == 3) & ((indivim['forter'] == 2) | (indivim['rstg'] == 1))
    indivim['actrec'][filter5] = 5
    filter7 = (indivim['acteu'] == 3) & ((indivim['retrai'] == 1) | (indivim['retrai'] == 2))
    indivim['actrec'][filter7] = 7
    indivim['actrec'][indivim['acteu'] == 3] =  8
    indivim['actrec'][indivim['acteu'].isnull()] =  9
    print indivim['actrec'].value_counts()
    # tu99
    if year == 2009:
        #erfind['tu99'] = None
        #eecind['tu99'] = float(eecind['tu99'])
        erfind['tu99'] = NaN

    ## locataire
    menagem["locataire"] = menagem["so"].isin([3,4,5])
    menagem["locataire"] = menagem["locataire"].astype("int32")

    transfert = indivim.ix[indivim['lpr'] == 1, ['ident', 'ddipl']]
    menagem  = menagem.merge(transfert)

    # correction
    def _manually_remove_errors():
        '''
        This method is here because some oddities can make it through the controls throughout the procedure
        It is here to remove all these individual errors that compromise the process.
        '''

        if year==2006:
            indivim.lien[indivim.noindiv==603018905] = 2
            indivim.noimer[indivim.noindiv==603018905] = 1
            print indivim[indivim.noindiv==603018905].to_string()

    _manually_remove_errors()

    # save
    save_temp(menagem, name="menagem", year=year)
    del erfmen, eecmen, menagem #, transfert
    print 'menagem saved'
    gc.collect()
    save_temp(indivim, name="indivim", year=year)
    del erfind, eecind
    print 'indivim saved'
    gc.collect()


def create_enfnn(year = 2006):
    '''
    '''

    erfs_survey_collection = SurveyCollection.load()
    survey = erfs_survey_collection.surveys['erfs_{}'.format(year)]

    ### Enfant à naître (NN pour nouveaux nés)
    individual_vars = ['noi', 'noicon', 'noindiv', 'noiper', 'noimer', 'ident', 'naia', 'naim', 'lien',
               'acteu','stc','contra','titc','mrec','forter','rstg','retrai','lpr','cohab','sexe',
               'agepr','rga']

    eeccmp1 = survey.get_values(table = "eec_cmp_1", variables = individual_vars)
    eeccmp2 = survey.get_values(table = "eec_cmp_2", variables = individual_vars)
    eeccmp3 = survey.get_values(table = "eec_cmp_3", variables = individual_vars)

    tmp = eeccmp1.merge(eeccmp2, how = "outer")
    enfnn = tmp.merge(eeccmp3, how = "outer")

    # optimisation des types? Controle de l'existence en passant
    # pourquoi pas des int quand c'est possible
    #TODO: minimal dtype TODO: shoudln't be here
    for var in individual_vars:
        print var
        enfnn[var] = enfnn[var].astype('float')
    del eeccmp1, eeccmp2, eeccmp3, individual_vars

    # création de variables
    print enfnn.describe()
    enfnn['declar1'] = ''
    enfnn['noidec'] = 0
    enfnn['ztsai'] = 0
    enfnn['year'] = year
    enfnn['year'] = enfnn['year'].astype("float32") # -> integer ?
    enfnn['agepf'] = enfnn['year'] - enfnn['naia']
    enfnn['agepf'][enfnn['naim'] >= 7] -= 1
    enfnn['actrec'] = 9
    enfnn['quelfic'] = 'ENF_NN'
    enfnn['persfip'] = ""

    #selection
    #enfnn <- enfnn[(enfnn$naia==enfnn$year & enfnn$naim>=10) | (enfnn$naia==enfnn$year+1 & enfnn$naim<=5),]
    enfnn = enfnn[
        (
            (enfnn['naia'] == enfnn['year']) & (enfnn['naim'] >= 10)
            ) | (
                    (enfnn['naia'] == enfnn['year'] + 1) & (enfnn['naim'] <= 5)
                    )
        ]
    #save
    save_temp(enfnn, name="enfnn", year=year)
    del enfnn
    print "enfnnm saved"
    gc.collect()


if __name__ == '__main__':
    print('Entering 01_pre_proc')
    import time
    deb = time.clock()
    year = 2006
    create_indivim(year = year)
    create_enfnn(year = year)
    print time.clock() - deb
