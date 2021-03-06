#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division

import logging
import numpy as np
import pandas as pd

from openfisca_core import periods
from openfisca_core.formula_helpers import switch
from openfisca_core.taxscales import MarginalRateTaxScale, combine_tax_scales
from openfisca_france import FranceTaxBenefitSystem
from openfisca_france.model.base import TypesCategorieSalarie, TAUX_DE_PRIME
from openfisca_france_data.utils import (
    assert_dtype,
    )
from openfisca_survey_manager.temporary import temporary_store_decorator

log = logging.getLogger(__name__)


smic_horaire_brut = {  # smic horaire brut moyen sur l'année
    2017: 9.76,
    2016: 9.67,
    2015: 9.61,
    2014: 9.53,
    2013: 9.43,
    2012: 9.4 * 6 / 12 + 9.22 * 6 / 12,
    2011: 9.19 * 1 / 12 + 9.0 * 11 / 12 ,
    2010: 8.86,
    }


# Sources BDM
smic_annuel_net_by_year = {
    2017: 12 * 1151.50,
    2016: 12 * 1141.61,
    2015: 12 * 1135.99,
    2014: 12 * 1128.70,
    2013: 12 * 1120.43,
    2012: 2 * 1116.87 + 4 * 1118.29 + 6 * 1096.88,
    2011: 11 * 1072.07 + 1094.71,
    2010: 12 * 1056.24,
    }


abattement_by_year = {
    2017: .0175,
    2016: .0175,
    2015: .0175,
    2014: .0175,
    2013: .0175,
    2012: .0175,
    2011: .03,
    2010: .03,
    }


def smic_annuel_imposbale_from_net(year):
    smic_net = smic_annuel_net_by_year[year]
    smic_brut = smic_horaire_brut[year] * 35 * 52
    smic_imposable = (
        smic_net
        + (.024 + 0.005) * (1 - abattement_by_year[year]) * smic_brut
        )
    return smic_imposable


smic_annuel_imposbale_by_year = dict([
    (year, smic_annuel_imposbale_from_net(year))
    for year in range(2010, 2018)
    ])


@temporary_store_decorator(file_name = 'erfs_fpr')
def build_variables_individuelles(temporary_store = None, year = None):
    """
    Création des variables individuelles
    """

    assert temporary_store is not None
    assert year is not None

    log.info('step_03_variables_individuelles: Création des variables individuelles')

    individus = temporary_store['individus_{}_post_01'.format(year)]

    old_by_new_variables = {
        'chomage_i': 'chomage_net',
        'pens_alim_recue_i': 'pensions_alimentaires_percues',
        'rag_i': 'rag_net',
        'retraites_i': 'retraite_nette',
        'ric_i': 'ric_net',
        'rnc_i': 'rnc_net',
        'salaires_i': 'salaire_net',
        }

    for variable in old_by_new_variables.keys():
        assert variable in individus.columns.tolist(), "La variable {} n'est pas présente".format(variable)

    individus.rename(
        columns = old_by_new_variables,
        inplace = True,
        )
    create_variables_individuelles(individus, year)
    temporary_store['individus_{}'.format(year)] = individus
    log.debug(u"step_03_variables_individuelles terminée")
    return individus


# helpers

def create_variables_individuelles(individus, year, survey_year = None):
    create_ages(individus, year)
    create_date_naissance(individus, age_variable = None, annee_naissance_variable = 'naia', mois_naissance = 'naim',
         year = year)
    create_activite(individus)
    revenu_type = 'net'
    period = periods.period(year)
    # create_revenus(individus, revenu_type = revenu_type)
    create_contrat_de_travail(individus, period = period, salaire_type = revenu_type, survey_year = survey_year)
    create_categorie_salarie(individus, period = period, suvrey_year = survey_year)
    tax_benefit_system = FranceTaxBenefitSystem()
    create_salaire_de_base(individus, period = period, revenu_type = revenu_type, tax_benefit_system = tax_benefit_system)
    create_effectif_entreprise(individus, period = period, survey_year = survey_year)
    create_statut_matrimonial(individus)


def create_activite(individus):
    """
    Création de la variable activite
    0 = Actif occupé
    1 = Chômeur
    2 = Étudiant, élève
    3 = Retraité
    4 = Autre inactif
    """
    create_actrec(individus)

    individus['activite'] = np.nan
    individus.loc[individus.actrec <= 3, 'activite'] = 0
    individus.loc[individus.actrec == 4, 'activite'] = 1
    individus.loc[individus.actrec == 5, 'activite'] = 2
    individus.loc[individus.actrec == 7, 'activite'] = 3
    individus.loc[individus.actrec == 8, 'activite'] = 4
    individus.loc[(individus.age <= 13) | (individus.actrec == 9), 'activite'] = 2
    message = "Valeurs prises par la variable activité \n {}".format(individus['activite'].value_counts(dropna = False))
    assert individus.activite.notnull().all(), message
    assert individus.activite.isin(range(5)).all(), message


def create_actrec(individus):
    """
    Création de la variables actrec
    pour activité recodée comme preconisé par l'INSEE p84 du guide méthodologique de l'ERFS
    """
    assert "actrec" not in individus.columns
    individus["actrec"] = np.nan
    # Attention : Pas de 6, la variable recodée de l'INSEE (voit p84 du guide methodo), ici \
    # la même nomenclature à été adopée
    # 3: contrat a durée déterminée
    individus.loc[individus.acteu == 1, 'actrec'] = 3
    # 8: femme (homme) au foyer, autre inactif
    individus.loc[individus.acteu == 3, 'actrec'] = 8
    # 1: actif occupé non salarié
    filter1 = (individus.acteu == 1) & (individus.stc.isin([1, 3]))  # actifs occupés non salariés à son compte
    individus.loc[filter1, 'actrec'] = 1                             # ou pour un membre de sa famille
    # 2: salarié pour une durée non limitée
    filter2 = (individus.acteu == 1) & (((individus.stc == 2) & (individus.contra == 1)) | (individus.titc == 2))
    individus.loc[filter2, 'actrec'] = 2
    # 4: au chomage
    filter4 = (individus.acteu == 2) | ((individus.acteu == 3) & (individus.mrec == 1))
    individus.loc[filter4, 'actrec'] = 4
    # 5: élève étudiant , stagiaire non rémunéré
    filter5 = (individus.acteu == 3) & ((individus.forter == 2) | (individus.rstg == 1))
    individus.loc[filter5, 'actrec'] = 5
    # 7: retraité, préretraité, retiré des affaires unchecked
    filter7 = (individus.acteu == 3) & ((individus.retrai == 1) | (individus.retrai == 2))
    individus.loc[filter7, 'actrec'] = 7
    # 9: probablement enfants de - de 16 ans TODO: check that fact in database and questionnaire
    individus.loc[individus.acteu == 0, 'actrec'] = 9

    assert individus.actrec.notnull().all()
    individus.actrec = individus.actrec.astype("int8")
    assert_dtype(individus.actrec, "int8")

    assert (individus.actrec != 6).all(), 'actrec ne peut pas être égale à 6'
    assert individus.actrec.isin(range(1, 10)).all(), 'actrec values are outside the interval [1, 9]'


def create_ages(individus, year = None):
    """
    Création des variables age et age_en_moi
    """
    assert year is not None
    individus['age'] = year - individus.naia - 1
    individus['age_en_mois'] = 12 * individus.age + 12 - individus.naim  # TODO why 12 - naim

    for variable in ['age', 'age_en_mois']:
        assert individus[variable].notnull().all(), "Il y a {} entrées non renseignées pour la variable {}".format(
            individus[variable].notnull().sum(), variable)


def create_categorie_salarie(individus, period, survey_year = None):
    """
    Création de la variable categorie_salarie:
      - "prive_non_cadre
      - "prive_cadre
      - "public_titulaire_etat
      - "public_titulaire_militaire
      - "public_titulaire_territoriale
      - "public_titulaire_hospitaliere
      - "public_non_titulaire
      - "non_pertinent"
    à partir des variables de l'eec' :
      - chpub :
          Variable déjà présente dans les fichiers antérieurs à 2013. Cependant, ses modalités ont été réordonnées ainsi :
          les modalités 1, 2, 3, 4, 5 et 6 de la variable CHPUB dans les fichiers antérieurs à 2013 correspondent à partir de
          2013 aux modalités respectives 3, 4, 5, 7, 2 et 1. Par ailleurs, une nouvelle modalité, Sécurité sociale (6) a été
          créée en 2013.
          A partir de (>=) 2013:
              1 Entreprise privée ou association
              2 Entreprise publique (EDF, La Poste, SNCF, etc.)
              3 État
              4 Collectivités territoriales
              5 Hôpitaux publics
              6 Sécurité sociale
              7 Particulier
          Avant (<) 2013:
              1 - Etat
              2 - Collectivités locales, HLM
              3 - Hôpitaux publics
              4 - Particulier
              5 - Entreprise publique (La Poste, EDF-GDF, etc.)
              6 - Entreprise privée, association

      - encadr : (encadrement de personnes)
          1 - Oui
          2 - Non

      - prosa (survey_year < 2013, voir qprcent)
          1 Manoeuvre ou ouvrier spécialisé
          2 Ouvrier qualifié ou hautement qualifié
          3 Technicien
          4 Employé de bureau, de commerce, personnel de services, personnel de catégorie C ou D
          5 Agent de maîtrise, maîtrise administrative ou commerciale VRP (non cadre) personnel de catégorie B
          7 Ingénieur, cadre (à l'exception des directeurs généraux ou de ses adjoints directs) personnel de catégorie A
          8 Directeur général, adjoint direct
          9 Autre

      - qprcent (survey_year >= 2013, voir prosa)
          Vide Sans objet (personnes non actives occupées, travailleurs informels et travailleurs intérimaires,
                          en activité temporaire ou d'appoint)
          1 Manoeuvre ou ouvrier spécialisé
          2 Ouvrier qualifié ou hautement qualifié, technicien d'atelier
          3 Technicien
          4 Employé de bureau, employé de commerce, personnel de services, personnel de catégorie C
          dans la fonction publique
          5 Agent de maîtrise, maîtrise administrative ou commerciale, VRP (non cadre), personnel de
          catégorie B dans la fonction publique
          6 Ingénieur, cadre (à l'exception des directeurs ou de leurs adjoints directs) personnel de
          catégorie A dans la fonction publique
          7 Directeur général, adjoint direct
          8 Autre
          9 Non renseigné

      - statut :
          11 - Indépendants
          12 - Employeurs
          13 - Aides familiaux
          21 - Intérimaires
          22 - Apprentis
          33 - CDD (hors Etat, coll.loc.), hors contrats aides
          34 - Stagiaires et contrats aides (hors Etat, coll.loc.)
          35 - Autres contrats (hors Etat, coll.loc.)
          43 - CDD (Etat, coll.loc.), hors contrats aides
          44 - Stagiaires et contrats aides (Etat, coll.loc.)
          45 - Autres contrats (Etat, coll.loc.)

      - titc :
          1 - Elève fonctionnaire ou stagiaire
          2 - Agent titulaire
          3 - Contractuel
    """

    # TODO: Est-ce que les stagiaires sont considérées comme des contractuels dans OF ?

    assert period is not None
    if survey_year is None:
        survey_year = period.start.year

    if survey_year >= 2013:
        log.debug('Using qprcent to infer prosa for year {}'.format(year))
        chpub_replacement = {
            0: 0,
            3: 1,
            4: 2,
            5: 3,
            7: 4,
            2: 5,
            1: 6,
            6: 1,
            }
        individus['chpub'] = individus.chpub.map(chpub_replacement)
        qprcent_to_prosa = {
            0: 0,
            1: 1,
            2: 2,
            3: 3,
            4: 4,
            5: 5,
            6: 7,
            7: 8,
            8: 9,
            9: 5, # On met les non renseignés en catégorie B
            }
        individus['prosa'] = individus.qprcent.map(qprcent_to_prosa)
    else:
        pass

    assert individus.chpub.isin(range(0, 7)).all(), \
        "chpub n'est pas toujours dans l'intervalle [0, 6]\n{}".format(individus.chpub.value_counts(dropna = False))

    individus.loc[individus.encadr == 0, 'encadr'] = 2
    assert individus.encadr.isin(range(1, 3)).all(), \
        "encadr n'est pas toujours dans l'intervalle [1, 2]\n{}".format(individus.encadr.value_counts(dropna = False))

    assert individus.prosa.isin(range(0, 10)).all(), \
        "prosa n'est pas toujours dans l'intervalle [0, 9]\n{}".format(individus.prosa.value_counts())

    statut_values = [0, 11, 12, 13, 21, 22, 33, 34, 35, 43, 44, 45, 99]
    assert individus.statut.isin(statut_values).all(), \
        "statut n'est pas toujours dans l'ensemble {} des valeurs antendues.\n{}".format(
            statut_values,
            individus.statut.value_counts(dropna = False)
            )

    if survey_year >= 2013:
        assert individus.titc.isin(range(5)).all(), \
            "titc n'est pas toujours dans l'ensemble [0, 1, 2, 3, 4] des valeurs antendues.\n{}".format(
                individus.titc.value_counts(dropna = False)
                )
    else:
        assert individus.titc.isin(range(4)).all(), \
            "titc n'est pas toujours dans l'ensemble [0, 1, 2, 3] des valeurs antendues.\n{}".format(
                individus.titc.value_counts(dropna = False)
                )

    chpub = individus.chpub
    titc = individus.titc

    # encadrement
    assert 'cadre' not in individus.columns
    individus['cadre'] = False
    individus.loc[individus.prosa.isin([7, 8]), 'cadre'] = True
    individus.loc[(individus.prosa == 9) & (individus.encadr == 1), 'cadre'] = True
    cadre = (individus.statut == 35) & (chpub > 3) & individus.cadre
    del individus['cadre']

    # etat_stag = (chpub==1) & (titc == 1)
    etat_titulaire = (chpub == 1) & ((titc == 2) | (titc == 1))
    etat_contractuel = (chpub == 1) & (titc == 3)

    militaire = False  # TODO:

    # collect_stag = (chpub==2) & (titc == 1)
    collectivites_locales_titulaire = (chpub == 2) & (titc == 2)
    collectivites_locales_contractuel = (chpub == 2) & (titc == 3)

    # hosp_stag = (chpub==2)*(titc == 1)
    hopital_titulaire = (chpub == 3) & (titc == 2)
    hopital_contractuel = (chpub == 3) & (titc == 3)

    contractuel = collectivites_locales_contractuel | hopital_contractuel | etat_contractuel

    if 'salaire_net' in individus and (individus['salaire_net'] > 0).any():
        actif_occupe = individus['salaire_net'] > 0
    elif 'salaire_imposable' in individus and (individus['salaire_imposable'] > 0).any():
        actif_occupe = individus['salaire_imposable'] > 0
    else:
        assert False, u'Pas de variable de salaire disponible non nuelle pour déterminer si un actif est occupé'

    #   TODO: may use something like this but to be improved and tested
    #    individus['categorie_salarie'] = np.select(
    #         [non_cadre, cadre, etat_titulaire, militaire, collectivites_locales_titulaire, hopital_titulaire,
    #               contractuel, non_pertinent]  # Choice list
    #         [0, 1, 2, 3, 4, 5, 6, 7],  # Condlist
    #         )

    individus['categorie_salarie'] = actif_occupe * (
        0 +
        1 * cadre +
        2 * etat_titulaire +
        3 * militaire +
        4 * collectivites_locales_titulaire +
        5 * hopital_titulaire +
        6 * contractuel
        ) + np.logical_not(actif_occupe) * 7

    log.debug('Les valeurs de categorie_salarie sont: \n {}'.format(
        individus['categorie_salarie'].value_counts(dropna = False)))
    assert individus['categorie_salarie'].isin(range(8)).all(), \
        "categorie_salarie n'est pas toujours dans l'intervalle [0, 7]\n{}".format(
            individus.categorie_salarie.value_counts(dropna = False))
    log.debug(u"Répartition des catégories de salariés: \n{}".format(
        individus
            .groupby(['contrat_de_travail'])['categorie_salarie']
            .value_counts(dropna = False)
            .sort_index()
        ))

def create_contrat_de_travail(individus, period, salaire_type = 'imposable'):
    """
    Création de la variable contrat_de_travail et heure_remunerees_volume
        0 - temps_plein
        1 - temps_partiel
        2 - forfait_heures_semaines
        3 - forfait_heures_mois
        4 - forfait_heures_annee
        5 - forfait_jours_annee
        6 - sans_objet
    à partir de la variables tppred (Temps de travail dans l'emploi principal)
        0 - Sans objet (ACTOP='2') ou non renseigné principal
        1 - Temps complet
        2 - Temps partiel
    qui doit être construite à partir de tpp
        0 - Sans objet (ACTOP='2') ou non renseigné
        1 - A temps complet
        2 - A temps partiel
        3 - Sans objet (pour les personnes non salariées qui estiment que cette question ne
            s'applique pas à elles)
    et de duhab
        1 - Temps partiel de moins de 15 heures
        2 - Temps partiel de 15 à 29 heures
        3 - Temps partiel de 30 heures ou plus
        4 - Temps complet de moins de 30 heures
        5 - Temps complet de 30 à 34 heures
        6 - Temps complet de 35 à 39 heures
        7 - Temps complet de 40 heures ou plus
        9 - Pas d'horaire habituel ou horaire habituel non déclaré
    On utilise également hcc pour déterminer le nombre d'heures
    TODO: utiliser la variable forfait
    """
    if not isinstance(period, periods.Period):
        period = periods.period(period)

    assert salaire_type in ['net', 'imposable']

    individus.loc[individus.hhc == 0, 'hhc'] = np.nan
    assert (
            (individus.hhc > 0) | individus.hhc.isnull()
        ).all()
    # assert individus.tppred.dtype is integer

    assert individus.tppred.isin(range(3)).all(), \
        'tppred values {} should be in [0, 1, 2]'.format(individus.tppred.unique())
    assert (
        individus.duhab.isin(range(10)) & (individus.duhab != 8)
        ).all(), 'duhab values {} should be in [0, 1, 2, 3, 4, 5, 6, 7, 9]'.format(individus.duhab.unique())

    individus['contrat_de_travail'] = 6  # sans objet par défaut
    if salaire_type == 'net':
        assert (individus.query('salaire_net == 0').contrat_de_travail == 6).all()
        log.info('Salaire retenu: {}'.format('salaire_net'))
        individus['salaire'] = individus.salaire_net.copy()
        smic = smic_annuel_net_by_year[period.start.year]

    elif salaire_type == 'imposable':
        individus['salaire_imposable'] = individus.salaire_imposable.fillna(0)
        assert (individus.query('salaire_imposable == 0').contrat_de_travail == 6).all()
        log.info('Salaire retenu: {}'.format('salaire_imposable'))
        individus['salaire'] = individus.salaire_imposable.copy()
        smic = smic_annuel_imposbale_by_year[period.start.year]

    if period.unit == 'month':
        smic = period.size * smic / 12

    # 0 On élimine les individus avec un salaire_net nul des salariés
    # 1. utilisation de tppred et durhab
    # 1.1  temps_plein
    individus.query('tppred == 1').duhab.value_counts(dropna = False)
    assert (individus.query('tppred == 1').duhab >= 4).all()
    individus.loc[
        (individus.salaire > 0) & (
            (individus.tppred == 1) | (individus.duhab.isin(range(4, 8)))
            ),
        'contrat_de_travail'
        ] = 0
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    # 1.2 temps partiel
    assert (individus.query('tppred == 2').duhab.isin([1, 2, 3, 9])).all()
    individus.loc[
        (individus.salaire > 0) & (
            (individus.tppred == 2) | (individus.duhab.isin(range(1, 4)))
            ),
        'contrat_de_travail'
        ] = 1
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    # 2. On traite les salaires horaires inféreurs au SMIC
    # 2.1 temps plein
    temps_plein = individus.query('(contrat_de_travail == 0) & (salaire > 0)')
    # (temps_plein.salaire > smic).value_counts()
    # temps_plein.query('salaire < 15000').salaire.hist()
    individus['heures_remunerees_volume'] = 0
    # On bascule à temps partiel et on réajuste les heures des temps plein qui ne touche pas le smic
    temps_plein_sous_smic = (
        (individus.contrat_de_travail == 0) &
        (individus.salaire > 0) &
        (individus.salaire < smic)
        )
    individus.loc[
        temps_plein_sous_smic,
        'contrat_de_travail'] = 1
    individus.loc[
        temps_plein_sous_smic,
        'heures_remunerees_volume'] = individus.loc[
            temps_plein_sous_smic,
            'salaire'
            ] / smic * 35
    assert (individus.loc[temps_plein_sous_smic, 'heures_remunerees_volume'] < 35).all()
    assert (individus.loc[temps_plein_sous_smic, 'heures_remunerees_volume'] > 0).all()
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    del temps_plein, temps_plein_sous_smic
    # 2.2 Pour les temps partiel on prends les heures hhc
    # On vérfie que celles qu'on a créées jusqu'ici sont correctes
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    assert (individus.query('(contrat_de_travail == 1) & (salaire > 0)').heures_remunerees_volume < 35).all()
    #
    axes = (individus.query('(contrat_de_travail == 1) & (salaire > 0)').hhc).hist(bins=100)
    axes.set_title("Heures (hhc)")
    # individus.query('(contrat_de_travail == 1) & (salaire > 0)').hhc.isnull().sum() = 489
    # 2.2.1 On abaisse le nombre d'heures pour que les gens touchent au moins le smic horaire
    temps_partiel = (individus.contrat_de_travail == 1) & (individus.salaire > 0)
    moins_que_smic_horaire_hhc = (
        ((individus.salaire / individus.hhc) < (smic / 35)) &
        individus.hhc.notnull()
        )
    # Si on dispose de la variable hhc on l'utilise
    individus.loc[
        temps_partiel & moins_que_smic_horaire_hhc,
        'heures_remunerees_volume'
        ] = individus.loc[
            temps_partiel & moins_que_smic_horaire_hhc,
            'salaire'
            ] / smic * 35
    individus.loc[
        temps_partiel & (~moins_que_smic_horaire_hhc) & individus.hhc.notnull(),
        'heures_remunerees_volume'
        ] = individus.loc[
            temps_partiel & (~moins_que_smic_horaire_hhc) & individus.hhc.notnull(),
            'hhc'
            ]
    axes = (individus
        .loc[temps_partiel]
        .query('(contrat_de_travail == 1) & (salaire > 0)')
        .heures_remunerees_volume
        .hist(bins=100)
        )
    axes.set_title("Heures (heures_remunerees_volume)")
    # 2.2.2 Il reste à ajuster le nombre d'heures pour les salariés à temps partiel qui n'ont pas de hhc
    # et qui disposent de moins que le smic_horaire ou de les basculer en temps plein sinon
    moins_que_smic_horaire_sans_hhc = (individus.salaire < smic) & individus.hhc.isnull()
    individus.loc[
        temps_partiel & moins_que_smic_horaire_sans_hhc,
        'heures_remunerees_volume'
        ] = individus.loc[
            temps_partiel & moins_que_smic_horaire_sans_hhc,
            'salaire'
            ] / smic * 35
    plus_que_smic_horaire_sans_hhc = (
        (individus.salaire >= smic) &
        individus.hhc.isnull()
        )
    individus.loc[
        temps_partiel & plus_que_smic_horaire_sans_hhc,
        'contrat_de_travail'
        ] = 0
    individus.loc[
        temps_partiel & plus_que_smic_horaire_sans_hhc,
        'heures_remunerees_volume'
        ] = 0
    # 2.2.3 On repasse en temps plein ceux qui ont plus de 35 heures par semaine
    temps_partiel_bascule_temps_plein = (
        temps_partiel &
        (individus.heures_remunerees_volume >= 35)
        )
    individus.loc[
        temps_partiel_bascule_temps_plein,
        'contrat_de_travail',
        ] = 0
    individus.loc[
        temps_partiel_bascule_temps_plein,
        'heures_remunerees_volume',
        ] = 0
    del temps_partiel, temps_partiel_bascule_temps_plein, moins_que_smic_horaire_hhc, moins_que_smic_horaire_sans_hhc
    assert (individus.query('contrat_de_travail == 0').heures_remunerees_volume == 0).all()
    assert (individus.query('contrat_de_travail == 1').heures_remunerees_volume < 35).all()
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    assert (individus.query('contrat_de_travail == 6').heures_remunerees_volume == 0).all()
    # 2.3 On traite ceux qui ont un salaire mais pas de contrat de travail renseigné
    # (temps plein ou temps complet)
    salarie_sans_contrat_de_travail = (
        (individus.salaire > 0) &
        ~individus.contrat_de_travail.isin([0, 1])
        )
    # 2.3.1 On passe à temps plein ceux qui ont un salaire supérieur au SMIC annuel
    individus.loc[
        salarie_sans_contrat_de_travail &
        (individus.salaire >= smic),
        'contrat_de_travail'
        ] = 0
    assert (individus.loc[
        salarie_sans_contrat_de_travail &
        (individus.salaire >= smic),
        'heures_remunerees_volume'
        ] == 0).all()
    # 2.3.2 On passe à temps partiel ceux qui ont un salaire inférieur au SMIC annuel
    individus.loc[
        salarie_sans_contrat_de_travail &
        (individus.salaire < smic),
        'contrat_de_travail'
        ] = 1
    individus.loc[
        salarie_sans_contrat_de_travail &
        (individus.salaire < smic),
        'heures_remunerees_volume'
        ] = individus.loc[
            salarie_sans_contrat_de_travail &
            (individus.salaire < smic),
            'salaire'
            ] / smic * 35
    # 2.3.3 On attribue des heures rémunérées aux individus à temps partiel qui ont un
    # salaire strictement positif mais un nombre d'heures travaillées nul
    salaire_sans_heures = (individus.contrat_de_travail == 1) & ~(individus.heures_remunerees_volume > 0)
    assert (individus.loc[salaire_sans_heures, 'salaire'] > 0).all()
    assert (individus.loc[salaire_sans_heures, 'duhab'] == 1).all()
    # Cela concerne peu de personnes qui ont par ailleurs duhab = 1 et un salaire supérieur au smic net.
    # On leur attribue donc un nombre d'heures travaillées égal à 15.
    individus.loc[
        salaire_sans_heures &
        (individus.salaire > smic),
        'heures_remunerees_volume'] = 15
    #
    individus.query('salaire > 0').contrat_de_travail.value_counts(dropna = False)
    individus.query('salaire == 0').contrat_de_travail.value_counts(dropna = False)

    individus.loc[salarie_sans_contrat_de_travail, 'salaire'].min()
    individus.loc[salarie_sans_contrat_de_travail, 'salaire'].hist(bins = 1000)
    del salarie_sans_contrat_de_travail
    # On vérifie que l'on n'a pas fait d'erreurs
    assert (individus.salaire >= 0).all(), u"Des salaires sont negatifs: {}".format(
            individus.loc[~(individus.salaire >= 0), 'salaire']
            )
    assert individus.contrat_de_travail.isin([0, 1, 6]).all()
    assert (individus.query('salaire > 0').contrat_de_travail.isin([0, 1])).all()
    assert (individus.query('salaire == 0').contrat_de_travail == 6).all()
    assert (individus.query('salaire == 0').heures_remunerees_volume == 0).all()
    assert (individus.query('contrat_de_travail in [0, 6]').heures_remunerees_volume == 0).all()
    assert (individus.query('contrat_de_travail == 1').heures_remunerees_volume < 35).all()
    assert (individus.heures_remunerees_volume >= 0).all()
    assert (individus.query('contrat_de_travail == 1').heures_remunerees_volume > 0).all(), \
        u"Des heures des temps partiels ne sont pas strictement positives: {}".format(
            individus.query('contrat_de_travail == 1').loc[
                ~(individus.query('contrat_de_travail == 1').heures_remunerees_volume > 0),
                'heures_remunerees_volume'
                ]
            )

    # la variable heures_remunerees_volume est nombre d'heures par semaine. On ajuste selon la période
    period = periods.period(period)
    if period.unit == 'year':
        individus['heures_remunerees_volume'] = individus.heures_remunerees_volume * 52
    elif period.unit == 'month':
        if period.size == 1:
            individus['heures_remunerees_volume'] = individus.heures_remunerees_volume * 52 / 12
        elif period.size == 3:
            individus['heures_remunerees_volume'] = individus.heures_remunerees_volume * 52 / 4
        else:
            log.error('Wrong period {}. Should be month or year'.format(period))
            raise
    else:
        log.error('Wrong period {}. Should be month or year'.format(period))
        raise

    del individus['salaire']

    return


def create_date_naissance(individus, age_variable = 'age', annee_naissance_variable = None, mois_naissance = None,
         year = None):
    """
    Création de la variable date_naissance à partir des variables age ou de l'année et du mois de naissance
    """
    assert year is not None
    assert bool(age_variable) != bool(annee_naissance_variable)  # xor

    random_state = np.random.RandomState(42)
    month_birth = 1 + random_state.randint(12, size = len(individus))
    day_birth = 1 + random_state.randint(28, size = len(individus))

    if age_variable is not None:
        assert age_variable in individus
        year_birth = (year - individus[age_variable]).astype(int)

    elif annee_naissance_variable is not None:
        year_birth = individus[annee_naissance_variable].astype(int)
        if mois_naissance is not None:
            month_birth = individus[mois_naissance].astype(int)

    individus['date_naissance'] = pd.to_datetime(
        pd.DataFrame({
            'year': year_birth,
            'month': month_birth,
            'day': day_birth,
            })
        )


def create_effectif_entreprise(individus, period = None, survey_year = None):
    """
    Création de la variable effectif_entreprise
    à partir de la variable nbsala qui prend les valeurs suivantes:
    Création de la variable effectif_entreprise à partir de la variable nbsala qui prend les valeurs suivantes:
    A partir de (>=) 2013
        0 Vide Sans objet ou non renseigné
        1 1 salarié
        2 2 salariés
        3 3 salariés
        4 4 salariés
        5 5 salariés
        6 6 salariés
        7 7 salariés
        8 8 salariés
        9 9 salariés
        10 10 à 49 salariés
        11 50 à 499 salariés
        12 500 salariés ou plus
        99 Ne sait pas
    Strictement avant (<) 2013
        0 - Non pertinent
        1 - Aucun salarié
        2 - 1 ou 4 salariés
        3 - 5 à 9 salariés
        4 - 10 à 19 salariés
        5 - 20 à 49 salariés
        6 - 50 à 199 salariés
        7 - 200 à 499 salariés
        8 - 500 à 999 salariés
        9 - 1000 salariés ou plus
        99 - Ne sait pas
    """
    assert period is not None
    if survey_year is None:
        survey_year = period.start.year

    if survey_year >= 2013:
        assert individus.nbsala.isin(range(0, 13) + [99]).all(), \
            "nbsala n'est pas toujours dans l'intervalle [0, 12] ou 99 \n{}".format(
                individus.nbsala.value_counts(dropna = False))
        individus['effectif_entreprise'] = np.select(
            [0, 1, 2, 3, 4, 5, 5, 7, 8, 9, 10, 50, 500],
            [
                individus.nbsala == 0,  # 0
                individus.nbsala == 1,  # 1
                individus.nbsala == 2,  # 2
                individus.nbsala == 3,  # 3
                individus.nbsala == 4,  # 4
                individus.nbsala == 5,  # 5
                individus.nbsala == 6,  # 6
                individus.nbsala == 7,  # 7
                individus.nbsala == 8,  # 8
                individus.nbsala == 9,  # 9
                individus.nbsala == 10,  # 9
                (individus.nbsala == 11) | (individus.nbsala == 99),  # 9
                individus.nbsala == 12,  # 9
                ]
            )
        assert individus.effectif_entreprise.isin([0, 1, 2, 3, 4, 5, 5, 7, 8, 9, 10, 50, 500]).all(), \
            "effectif_entreprise n'est pas toujours dans [0, 1, 5, 10, 20, 50, 200, 500, 1000] \n{}".format(
                individus.effectif_entreprise.value_counts(dropna = False))

    else:
        assert individus.nbsala.isin(range(0, 10) + [99]).all(), \
            "nbsala n'est pas toujours dans l'intervalle [0, 9] ou 99 \n{}".format(
                individus.nbsala.value_counts(dropna = False))
        individus['effectif_entreprise'] = np.select(
            [0, 1, 5, 10, 20, 50, 200, 500, 1000],
            [
                individus.nbsala.isin([0, 1]),  # 0
                individus.nbsala == 2,  # 1
                individus.nbsala == 3,  # 5
                individus.nbsala == 4,  # 10
                individus.nbsala == 5,  # 20
                (individus.nbsala == 6) | (individus.nbsala == 99),  # 50
                individus.nbsala == 7,  # 200
                individus.nbsala == 8,  # 500
                individus.nbsala == 9,  # 1000
                ]
            )

        assert individus.effectif_entreprise.isin([0, 1, 5, 10, 20, 50, 200, 500, 1000]).all(), \
            "effectif_entreprise n'est pas toujours dans [0, 1, 5, 10, 20, 50, 200, 500, 1000] \n{}".format(
                individus.effectif_entreprise.value_counts(dropna = False))


def create_revenus(individus, revenu_type = 'imposable'):
    """
    Création des variables:
        chomage_net,
        pensions_alimentaires_percues,
        rag_net,
        retraite_nette,
        ric_net,
        rnc_net,
    et éventuellement, si revenus_type = 'imposable' des variables:
        chomage_imposable,
        rag,
        retraite_imposable,
        ric,
        rnc,
        salaire_imposable,
    """

    if revenu_type == 'imposable':
        variables = [
            # 'pension_alimentaires_percues',
            'chomage_imposable',
            'retraite_imposable',
            ]
        for variable in variables:
            assert variable in individus.columns.tolist(), "La variable {} n'est pas présente".format(variable)

        for variable in variables:
            if (individus[variable] < 0).any():

                negatives_values = individus[variable].value_counts().loc[individus[variable].value_counts().index < 0]
                log.debug("La variable {} contient {} valeurs négatives\n {}".format(
                    variable,
                    negatives_values.sum(),
                    negatives_values,
                    )
                )
        #
        # csg des revenus de replacement
        # 0 - Non renseigné/non pertinent
        # 1 - Exonéré
        # 2 - Taux réduit
        # 3 - Taux plein
        taux = pd.concat(
            [
                individus.csgrstd_i / individus.retraite_brute,
                individus.csgchod_i / individus.chomage_brut,
            ],
            axis=1
            ).max(axis = 1)
        # taux.loc[(0 < taux) & (taux < .1)].hist(bins = 100)
        individus['taux_csg_remplacement'] = np.select(
            [
                taux.isnull(),
                taux.notnull() & (taux < 0.021),
                taux.notnull() & (taux > 0.021) & (taux < 0.0407),
                taux.notnull() & (taux > 0.0407)
                ],
            [0, 1, 2, 3]
            )
        for value in [0, 1, 2, 3]:
            assert (individus.taux_csg_remplacement == value).any(), \
                "taux_csg_remplacement ne prend jamais la valeur {}".format(value)
        assert individus.taux_csg_remplacement.isin(range(4)).all()


def create_salaire_de_base(individus, period = None, revenu_type = 'imposable', tax_benefit_system = None):
    """Calcule la variable salaire_de_base à partir du salaire imposable par inversion du barème
    de cotisations sociales correspondant à la catégorie à laquelle appartient le salarié.
    """
    assert period is not None
    assert revenu_type in ['net', 'imposable']
    for variable in ['categorie_salarie', 'contrat_de_travail', 'heures_remunerees_volume']:
        assert variable in individus.columns, "{} is missing".format(variable)
    assert tax_benefit_system is not None

    if revenu_type == 'imposable':
        salaire_pour_inversion = individus.salaire_imposable
    else:
        salaire_pour_inversion = individus.salaire_net

    categorie_salarie = individus.categorie_salarie
    contrat_de_travail = individus.contrat_de_travail
    heures_remunerees_volume = individus.heures_remunerees_volume
    # hsup = simulation.calculate('hsup', period = this_year)

    parameters = tax_benefit_system.get_parameters_at_instant(period.start)
    salarie = parameters.cotsoc.cotisations_salarie
    plafond_securite_sociale_mensuel = parameters.cotsoc.gen.plafond_securite_sociale
    parameters_csg_deductible = parameters.prelevements_sociaux.contributions.csg.activite.deductible
    taux_csg = parameters_csg_deductible.taux
    taux_abattement = parameters_csg_deductible.abattement.rates[0]
    try:
        seuil_abattement = parameters_csg_deductible.abattement.thresholds[1]
    except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours était limité à 4 PSS
        seuil_abattement = None
    csg_deductible = MarginalRateTaxScale(name = 'csg_deductible')
    csg_deductible.add_bracket(0, taux_csg * (1 - taux_abattement))
    if seuil_abattement is not None:
        csg_deductible.add_bracket(seuil_abattement, taux_csg)

    if revenu_type == 'net':  # On ajoute CSG imposable et crds
        # csg imposable
        parameters_csg_imposable = parameters.prelevements_sociaux.contributions.csg.activite.imposable
        taux_csg = parameters_csg_imposable.taux
        taux_abattement = parameters_csg_imposable.abattement.rates[0]
        try:
            seuil_abattement = parameters_csg_imposable.abattement.thresholds[1]
        except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours était limité à 4 PSS
            seuil_abattement = None
        csg_imposable = MarginalRateTaxScale(name = 'csg_imposable')
        csg_imposable.add_bracket(0, taux_csg * (1 - taux_abattement))
        if seuil_abattement is not None:
            csg_imposable.add_bracket(seuil_abattement, taux_csg)
        # crds
        # csg imposable
        parameters_crds = parameters.prelevements_sociaux.contributions.crds.activite
        taux_csg = parameters_crds.taux
        taux_abattement = parameters_crds.abattement.rates[0]
        try:
            seuil_abattement = parameters_crds.abattement.thresholds[1]
        except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours était limité à 4 PSS
            seuil_abattement = None
        crds = MarginalRateTaxScale(name = 'crds')
        crds.add_bracket(0, taux_csg * (1 - taux_abattement))
        if seuil_abattement is not None:
            crds.add_bracket(seuil_abattement, taux_csg)

    # Check baremes
    target = dict()
    target['prive_non_cadre'] = set(['maladie', 'arrco', 'vieillesse_deplafonnee', 'vieillesse', 'agff', 'assedic'])
    target['prive_cadre'] = set(
        ['maladie', 'arrco', 'vieillesse_deplafonnee', 'agirc', 'cet', 'apec', 'vieillesse', 'agff', 'assedic']
        )
    target['public_non_titulaire'] = set(['excep_solidarite', 'maladie', 'ircantec', 'vieillesse_deplafonnee', 'vieillesse'])

    for categorie in ['prive_non_cadre', 'prive_cadre', 'public_non_titulaire']:
        baremes_collection = salarie[categorie]
        baremes_to_remove = list()
        for name, bareme in baremes_collection._children.iteritems():
            if name.endswith('alsace_moselle'):
                baremes_to_remove.append(name)
        for name in baremes_to_remove:
            del baremes_collection._children[name]

    for categorie in ['prive_non_cadre', 'prive_cadre', 'public_non_titulaire']:
        test = set(
            name for name, bareme in salarie[categorie]._children.iteritems()
            if isinstance(bareme, MarginalRateTaxScale)
            )
        assert target[categorie] == test, 'target: {} \n test {}'.format(target[categorie], test)
    del bareme

    # On ajoute la CSG deductible et on proratise par le plafond de la sécurité sociale
    # Pour éviter les divisions 0 /0 dans le switch qui sert à calculer le salaire_pour_inversion_proratise
    if period.unit == 'year':
        plafond_securite_sociale = plafond_securite_sociale_mensuel * 12
        heures_temps_plein = 52 * 35
    elif period.unit == 'month':
        plafond_securite_sociale = plafond_securite_sociale_mensuel * period.size
        heures_temps_plein = (52 * 35 / 12) * period.size
    else:
        raise

    heures_remunerees_volume_avoid_warning = heures_remunerees_volume + (heures_remunerees_volume == 0) * 1e9
    salaire_pour_inversion_proratise = switch(
        contrat_de_travail,
        {
            # temps plein
            0: salaire_pour_inversion / plafond_securite_sociale,
            # temps partiel
            1: salaire_pour_inversion / (
                (heures_remunerees_volume_avoid_warning / heures_temps_plein) * plafond_securite_sociale
                ),
            }
        )

    def add_agirc_gmp_to_agirc(agirc, parameters):
        plafond_securite_sociale_annuel = parameters.cotsoc.gen.plafond_securite_sociale * 12
        salaire_charniere = parameters.prelevements_sociaux.gmp.salaire_charniere_annuel / plafond_securite_sociale_annuel
        cotisation = parameters.prelevements_sociaux.gmp.cotisation_forfaitaire_mensuelle_en_euros.part_salariale * 12
        n = (cotisation + 1) * 12
        agirc.add_bracket(n / plafond_securite_sociale_annuel, 0)
        agirc.rates[0] = cotisation / n
        agirc.thresholds[2] = salaire_charniere

    salaire_de_base = 0.0
    for categorie in ['prive_non_cadre', 'prive_cadre', 'public_non_titulaire']:
        if categorie == 'prive_cadre':
            add_agirc_gmp_to_agirc(salarie[categorie].agirc, parameters)

        bareme = combine_tax_scales(salarie[categorie])
        bareme.add_tax_scale(csg_deductible)
        if revenu_type == 'net':
            bareme.add_tax_scale(csg_imposable)
            bareme.add_tax_scale(crds)

        assert bareme.inverse().thresholds[0] == 0, "Invalid inverse bareme for {}:\n {}".format(
            categorie, bareme.inverse())
        for rate in bareme.inverse().rates:
            assert rate > 0

        brut_proratise = bareme.inverse().calc(salaire_pour_inversion_proratise)
        assert np.isfinite(brut_proratise).all()
        brut = plafond_securite_sociale * switch(
            contrat_de_travail,
            {
                # temps plein
                0: brut_proratise,
                # temps partiel
                1: brut_proratise * (heures_remunerees_volume / (heures_temps_plein)),
                }
            )
        salaire_de_base += (
            (categorie_salarie == TypesCategorieSalarie[categorie].index) * brut
            )
        if (categorie_salarie == TypesCategorieSalarie[categorie].index).any():
            log.debug("Pour {} : brut = {}".format(TypesCategorieSalarie[categorie].index, brut))
            log.debug('bareme direct: {}'.format(bareme))

    # agirc_gmp
    # gmp = P.prelevements_sociaux.gmp
    # salaire_charniere = gmp.salaire_charniere_annuel
    # cotisation_forfaitaire = gmp.cotisation_forfaitaire_mensuelle_en_euros.part_salariale * 12
    # salaire_de_base += (
    #     (categorie_salarie == CATEGORIE_SALARIE['prive_cadre']) *
    #     (salaire_de_base <= salaire_charniere) *
    #     cotisation_forfaitaire
    #     )
    individus['salaire_de_base'] = salaire_de_base


def create_traitement_indiciaire_brut(individus, period = None, revenu_type = 'imposable',
            tax_benefit_system = None):
    """
    Calcule le tratement indiciaire brut à partir du salaire imposable ou du salaire net.
    Note : le supplément familial de traitement est imposable. Pas géré
    """
    assert period is not None
    assert revenu_type in ['net', 'imposable']
    assert tax_benefit_system is not None

    for variable in ['categorie_salarie', 'contrat_de_travail', 'heures_remunerees_volume']:
        assert variable in individus.columns

    if revenu_type == 'imposable':
        assert 'salaire_imposable' in individus.columns
        salaire_pour_inversion = individus.salaire_imposable
    else:
        assert 'salaire_net' in individus.columns
        salaire_pour_inversion = individus.salaire_net

    categorie_salarie = individus.categorie_salarie
    contrat_de_travail = individus.contrat_de_travail
    heures_remunerees_volume = individus.heures_remunerees_volume

    legislation = parameters = tax_benefit_system.get_parameters_at_instant(period.start)

    salarie = legislation.cotsoc.cotisations_salarie
    plafond_securite_sociale_mensuel = legislation.cotsoc.gen.plafond_securite_sociale
    legislation_csg_deductible = legislation.prelevements_sociaux.contributions.csg.activite.deductible
    taux_csg = legislation_csg_deductible.taux
    taux_abattement = legislation_csg_deductible.abattement.rates[0]
    try:
        seuil_abattement = legislation_csg_deductible.abattement.thresholds[1]
    except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours été limité à 4 PSS
        seuil_abattement = None
    csg_deductible = MarginalRateTaxScale(name = 'csg_deductible')
    csg_deductible.add_bracket(0, taux_csg * (1 - taux_abattement))
    if seuil_abattement is not None:
        csg_deductible.add_bracket(seuil_abattement, taux_csg)

    if revenu_type == 'net':
        # Cas des revenus nets:
        # comme les salariés du privé, on ajoute CSG imposable et crds qui s'appliquent à tous les revenus
        # 1. csg imposable
        legislation_csg_imposable = legislation.prelevements_sociaux.contributions.csg.activite.imposable
        taux_csg = legislation_csg_imposable.taux
        taux_abattement = legislation_csg_imposable.abattement.rates[0]
        try:
            seuil_abattement = legislation_csg_imposable.abattement.thresholds[1]
        except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours été limité à 4 PSS
            seuil_abattement = None
        csg_imposable = MarginalRateTaxScale(name = 'csg_imposable')
        csg_imposable.add_bracket(0, taux_csg * (1 - taux_abattement))
        if seuil_abattement is not None:
            csg_imposable.add_bracket(seuil_abattement, taux_csg)
        # 2. crds
        legislation_crds = legislation.prelevements_sociaux.contributions.crds.activite
        taux_csg = legislation_crds.taux
        taux_abattement = legislation_crds.abattement.rates[0]
        try:
            seuil_abattement = legislation_crds.abattement.thresholds[1]
        except IndexError:  # Pour gérer le fait que l'abattement n'a pas toujours été limité à 4 PSS
            seuil_abattement = None
        crds = MarginalRateTaxScale(name = 'crds')
        crds.add_bracket(0, taux_csg * (1 - taux_abattement))
        if seuil_abattement is not None:
            crds.add_bracket(seuil_abattement, taux_csg)

    # Check baremes
    target = dict()
    target['public_titulaire_etat'] = set(['excep_solidarite', 'pension', 'rafp'])
    target['public_titulaire_hospitaliere'] = set(['excep_solidarite', 'cnracl1', 'rafp'])
    target['public_titulaire_territoriale'] = set(['excep_solidarite', 'cnracl1', 'rafp'])

    categories_salarie_du_public = [
        'public_titulaire_etat',
        'public_titulaire_hospitaliere',
        'public_titulaire_territoriale',
        ]

    for categorie in categories_salarie_du_public:
        baremes_collection = salarie[categorie]
        test = set(
            name for name, bareme in salarie[categorie]._children.iteritems()
            if isinstance(bareme, MarginalRateTaxScale) and name != 'cnracl2'
            )
        assert target[categorie] == test, 'target for {}: \n  target = {} \n  test = {}'.format(categorie, target[categorie], test)

    # Barèmes à éliminer :
        # cnracl1 = taux hors NBI -> OK
        # cnracl2 = taux NBI -> On ne le prend pas en compte pour l'instant
    for categorie in [
        'public_titulaire_hospitaliere',
        'public_titulaire_territoriale',
        ]:
        baremes_collection = salarie[categorie]
        baremes_to_remove = list()
        baremes_to_remove.append('cnracl2')
        for name in baremes_to_remove:
            if 'cnracl2' in baremes_collection:
                del baremes_collection._children[name]

    salarie = salarie._children.copy()
    # RAFP des agents titulaires
    for categorie in categories_salarie_du_public:
        baremes_collection = salarie[categorie]
        baremes_collection['rafp'].multiply_rates(TAUX_DE_PRIME, inplace = True)

    # On ajoute la CSG déductible et on proratise par le plafond de la sécurité sociale
    if period.unit == 'year':
        plafond_securite_sociale = plafond_securite_sociale_mensuel * 12
        heures_temps_plein = 52 * 35
    elif period.unit == 'month':
        plafond_securite_sociale = plafond_securite_sociale_mensuel * period.size
        heures_temps_plein = (52 * 35 / 12) * period.size
    else:
        raise

    # Pour a fonction publique la csg est calculée sur l'ensemble salbrut(=TIB) + primes
    # Imposable = (1 + taux_prime) * TIB - csg[(1 + taux_prime) * TIB] - pension[TIB]
    for categorie in categories_salarie_du_public:
        bareme_csg_deduc_public = csg_deductible.multiply_rates(
            1 + TAUX_DE_PRIME, inplace = False, new_name = "csg deduc public")
        if revenu_type == 'net':
            bareme_csg_imp_public = csg_imposable.multiply_rates(
                1 + TAUX_DE_PRIME, inplace = False, new_name = "csg imposable public")
            bareme_crds_public = crds.multiply_rates(
                1 + TAUX_DE_PRIME, inplace = False, new_name = "crds public")

    for categorie in categories_salarie_du_public:
        bareme_prime = MarginalRateTaxScale(name = "taux de prime")
        bareme_prime.add_bracket(0, -TAUX_DE_PRIME)  # barème équivalent à taux_prime*TIB

    heures_remunerees_volume_avoid_warning = heures_remunerees_volume + (heures_remunerees_volume == 0) * 1e9
    salaire_pour_inversion_proratise = switch(
        contrat_de_travail,
        {
            # temps plein
            0: salaire_pour_inversion / plafond_securite_sociale,
            # temps partiel
            1: salaire_pour_inversion / (
                (heures_remunerees_volume_avoid_warning / heures_temps_plein) * plafond_securite_sociale
                ),
            }
        )

    traitement_indiciaire_brut = 0.0

    for categorie in categories_salarie_du_public:
        for key, value in salarie[categorie]._children.iteritems():
            log.debug(key, value)
        bareme = combine_tax_scales(salarie[categorie])
        log.debug('bareme cotsoc : {}'.format(bareme))
        bareme.add_tax_scale(bareme_csg_deduc_public)
        log.debug('bareme cotsoc + csg_deduc: {}'.format(bareme))
        if revenu_type == 'net':
            bareme.add_tax_scale(bareme_csg_imp_public)
            log.debug('bareme cotsoc + csg_deduc + csg_imp: {}'.format(bareme))
            bareme.add_tax_scale(bareme_crds_public)
            log.debug('bareme cotsoc + csg_deduc + csg_imp + crds: {}'.format(bareme))

        bareme.add_tax_scale(bareme_prime)
        log.debug('bareme cotsoc + csg_deduc + csg_imp + crds + prime: {}'.format(bareme))

        brut_proratise = bareme.inverse().calc(salaire_pour_inversion_proratise)

        assert np.isfinite(brut_proratise).all()
        brut = plafond_securite_sociale * switch(
            contrat_de_travail,
            {
                # temps plein
                0: brut_proratise,
                # temps partiel
                1: brut_proratise * (heures_remunerees_volume / (heures_temps_plein)),
                }
            )
        traitement_indiciaire_brut += (
            (categorie_salarie == TypesCategorieSalarie[categorie].index) * brut
            )
        if (categorie_salarie == TypesCategorieSalarie[categorie].index).any():
            log.debug("Pour {} : brut = {}".format(TypesCategorieSalarie[categorie].index, brut))
            log.debug('bareme direct: {}'.format(bareme))

    # TODO: complete this to deal with the fonctionnaire
    # supp_familial_traitement = 0  # TODO: dépend de salbrut
    # indemnite_residence = 0  # TODO: fix bug
    individus['traitement_indiciaire_brut'] = traitement_indiciaire_brut
    individus['primes_fonction_publique'] = TAUX_DE_PRIME * traitement_indiciaire_brut


def create_statut_matrimonial(individus):
    u"""
    Création de la variable statut_marital qui prend les valeurs:
      1 - "Marié",
      2 - "Célibataire",
      3 - "Divorcé",
      4 - "Veuf",
      5 - "Pacsé",
      6 - "Jeune veuf"
    à partir de la variable matri qui prend les valeurs
      0 - sans objet (moins de 15 ans)
      1 - Célibataire
      2 - Marié(e) ou remarié(e)
      3 - Veuf(ve)
      4 - Divorcé(e)
    """
    assert individus.matri.isin(range(5)).all()

    individus['statut_marital'] = 2  # célibataire par défaut
    individus.loc[individus.matri == 2, 'statut_marital'] = 1  # marié(e)
    individus.loc[individus.matri == 3, 'statut_marital'] = 4  # veuf(ve)
    individus.loc[individus.matri == 4, 'statut_marital'] = 3  # divorcé(e)

    assert individus.statut_marital.isin(range(1, 7)).all()


def todo_create(individus):
    log.debug(u"    6.3 : variable txtppb")
    individus.loc[individus.txtppb.isnull(), 'txtppb'] = 0
    assert individus.txtppb.notnull().all()
    log.debug("Valeurs prises par la variable txtppb \n {}".format(
        individus['txtppb'].value_counts(dropna = False)))


if __name__ == '__main__':

    import sys
    logging.basicConfig(level = logging.INFO, stream = sys.stdout)
    # logging.basicConfig(level = logging.INFO,  filename = 'step_03.log', filemode = 'w')
    year = 2012

    #    from openfisca_france_data.erfs_fpr.input_data_builder import step_01_preprocessing
    #    step_01_preprocessing.build_merged_dataframes(year = year)
    individus = build_variables_individuelles(year = year)
