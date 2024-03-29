import os
import pandas as pd
import numpy as np
import re
import shap
import matplotlib.pyplot as plt
from Sklearn_NestedCV.master.Statistical_analysis.nested_cv import NestedCV
from Sklearn_NestedCV.master.Statistical_analysis.data_harmonization import *
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.neural_network import MLPClassifier
import imblearn
from sklearn.metrics import accuracy_score, roc_auc_score
import time
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from itertools import combinations
from sklearn.utils.fixes import loguniform
from scipy.stats import randint
from joblib import dump, load
from natsort import natsorted


def save_results(save_dir, clf, X, y, score):
    outer_results = clf.outer_results
    outer_results.update({'outer_test_accuracy': [], 'outer_test_sensitivity': [], 'outer_test_specificity': []})
    model_pickle_dir = os.path.join(save_dir, 'Pickled_model')
    os.makedirs(model_pickle_dir, exist_ok=True)
    if not 'auc' in score:
        outer_results.update({'outer_test_auc': []})
    for i, model in enumerate(clf.outer_pred['model']):
        train_index = clf.outer_pred['train'][i]
        test_index = clf.outer_pred['test'][i]
        y_true, y_pred = y.to_numpy()[test_index], model.predict(X.to_numpy()[test_index])
        outer_results['outer_test_accuracy'].append(accuracy_score(y_true, y_pred))
        if len(np.unique(y)) == 2:
            sensitivity = imblearn.metrics.sensitivity_score(y_true, y_pred, average='binary', pos_label=1)
            specificity = imblearn.metrics.specificity_score(y_true, y_pred, average='binary', pos_label=1)
        else:
            sensitivity = imblearn.metrics.sensitivity_score(y_true, y_pred, average='macro')
            specificity = imblearn.metrics.specificity_score(y_true, y_pred, average='macro')
        outer_results['outer_test_sensitivity'].append(sensitivity)
        outer_results['outer_test_specificity'].append(specificity)
        if not 'auc' in score:
            if len(np.unique(y)) == 2:
                if hasattr(model[-1], 'decision_fuction'):
                    y_score = model.decision_function(X.to_numpy()[clf.outer_pred['test'][i]])
                else:
                    y_score = model.predict_proba(X.to_numpy()[clf.outer_pred['test'][i]])[:, 1].ravel()
                outer_results['outer_test_auc'].append(roc_auc_score(y_true, y_score, labels=[0, 1]))
            else:
                y_score = model.predict_proba(X.to_numpy()[clf.outer_pred['test'][i]])
                outer_results['outer_test_auc'].append(roc_auc_score(y_true, y_score, average='macro', multi_class='ovo', labels=[1, 2, 3]))
        fit_dic = {'Model': model, 'X_train': X.to_numpy()[train_index], 'y_train': y.to_numpy()[train_index],
                   'X_test': X.to_numpy()[test_index], 'y_test': y.to_numpy()[test_index],
                   'score': clf.outer_results['outer_test_score'][i]}
        dump(fit_dic, os.path.join(model_pickle_dir, 'joblib_model_with_info_outer%s.pkl' % i))
    inner_results_reformated = {'inner_Fold': [], 'params': [], 'mean_test_score': [], 'std_test_score': [],
                                'mean_train_score': [], 'std_train_score': []}
    for i, fold_results in enumerate(clf.inner_results):
        for j in range(len(fold_results['params'])):
            inner_results_reformated['inner_Fold'].append(i)
            for key in fold_results.keys():
                inner_results_reformated[key].append(fold_results[key][j])
    results = {'outer': clf.outer_results, 'inner': inner_results_reformated}
    with pd.ExcelWriter(os.path.join(save_dir, 'NestedCV_results.xlsx')) as writer:
        for loop in results:
            df = pd.DataFrame(results[loop])
            df.to_excel(writer, sheet_name=loop)
    # Extract selected feature for each outer model
    with pd.ExcelWriter(os.path.join(save_dir, 'Selected_features.xlsx')) as writer:
        for i, model in enumerate(clf.outer_pred['model']):
            if 'DimensionalityReduction' in model.named_steps.keys():
                if isinstance(model['DimensionalityReduction'].dr_func, PCA):
                    coefficient_matrix = model['DimensionalityReduction'].dr_func.components_
                else:
                    coefficient_matrix = model['DimensionalityReduction'].coefficient_matrix
                df_dr = pd.DataFrame(coefficient_matrix)
                df_dr.index = ['Dr_Feature' + str(_ + 1) for _ in range(coefficient_matrix.shape[0])]
                df_dr.columns = X.columns
                if 'FeatureSelection' in model.named_steps.keys():
                    df_dr['Selected_features'] = model['FeatureSelection'].get_support()
                elif 'SelectFromModel' in model.named_steps.keys():
                    df_dr['Selected_features'] = model['SelectFromModel'].get_support()
                df_dr.to_excel(writer, sheet_name='outer%d' % (i + 1))
            else:
                if 'FeatureSelection' in model.named_steps.keys():
                    df_dr = pd.DataFrame({'Selected_features': model['FeatureSelection'].get_support()})
                    df_dr.index = X.columns
                    df_dr.to_excel(writer, sheet_name='outer%d' % (i + 1))
                elif 'SelectFromModel' in model.named_steps.keys():
                    df_dr = pd.DataFrame({'Selected_features': model['SelectFromModel'].get_support()})
                    df_dr.index = X.columns
                    df_dr.to_excel(writer, sheet_name='outer%d' % (i + 1))
                else:
                    pd.DataFrame().to_excel(writer, sheet_name='outer%d' % (i + 1))
    # Extract predictions from model
    df = pd.DataFrame({key: clf.outer_pred[key] for key in clf.outer_pred if key != 'model'}, dtype='object')
    df.to_excel(os.path.join(save_dir, 'Model_predictions.xlsx'))


def format_feature_name(feature_name):
    del_substrings = ['original_']
    if feature_name.endswith('TBR'):
        del_substrings += ['TBR']
    if 'ratio' in feature_name:
        del_substrings += ['_ratio']
    if 'merged' in feature_name:
        del_substrings += ['_.3D._merged._']
    else:
        del_substrings += ['_.3D._']
    for substring in del_substrings:
        feature_name = feature_name.replace(substring, '')
    feature_name = feature_name.replace('.', '_')
    if 'matrix' in feature_name:
        matrix_name = feature_name.split('matrix')[0]
        if 'occurrence' in matrix_name:
            matrix_name = 'GLCM'
        elif 'Run_length' in matrix_name:
            matrix_name = 'GLRLM'
        elif 'Size_zone' in matrix_name:
            matrix_name = 'GLSZM'
        elif 'Neighbourhood_grey_tone_difference' in matrix_name:
            matrix_name = 'NGTDM'
        elif 'Neighbouring_grey_level_dependence' in matrix_name:
            matrix_name = 'NGLDM'
        feature_name = '_'.join([matrix_name, feature_name.split('matrix')[1]])
    feature_name.replace('__', '_')
    if feature_name[-1] == '_':
        feature_name = feature_name[:-1]
    return feature_name


def get_models_randomsearch(output, algo_list, dr_list, fs_list):
    seed = 111
    models_dic = {}
    for algo_name in algo_list:
        for dim_red in dr_list:
            for fs in fs_list:
                if algo_name == 'LR_L2':
                    algo = LogisticRegression
                    classifier_options = {'penalty': 'l2', 'random_state': seed, 'solver': 'saga', 'max_iter': 1e6,
                                          'class_weight': 'balanced'}
                    classifier_params = {'C': loguniform(1e-3, 1e3)}
                elif algo_name == 'SVC_linear':
                    algo = SVC
                    classifier_options = {'kernel': 'linear', 'probability': True, 'random_state': seed,
                                          'cache_size': 1e4, 'max_iter': 1e6,
                                          'class_weight': 'balanced'}
                    classifier_params = {'C': loguniform(10 ** -4, 10 ** 4)}
                elif algo_name == 'SVC_RBF':
                    algo = SVC
                    classifier_options = {'kernel': 'rbf', 'probability': True, 'random_state': seed,
                                          'cache_size': 1e4, 'max_iter': 1e6,
                                          'class_weight': 'balanced'}
                    classifier_params = {'C': loguniform(10 ** -4, 10 ** 4),
                                         'gamma': loguniform(10 ** -4, 10 ** 4)}
                elif algo_name == 'RF':
                    algo = RandomForestClassifier
                    classifier_options = {'n_estimators': 500, 'random_state': seed,
                                          'class_weight': 'balanced'}
                    classifier_params = {'max_features': np.arange(0.1, 1, 0.1),
                                         'min_samples_split': np.arange(0.1, 1, 0.1)}
                elif algo_name == 'Nnet':
                    algo = MLPClassifier
                    classifier_options = {'max_iter': int(1e6), 'random_state': seed}
                    classifier_params = {'hidden_layer_sizes': randint(2, 40),
                                         'alpha': loguniform(1e-4, 1e3),
                                         'learning_rate_init': loguniform(1e-4, 1e-2)}
                name_suffix = '%s_%s' % (fs, algo_name)
                params_dic = {'classifier': classifier_params}

                if dim_red == 'Leger':
                    pipeline_options = {'DimensionalityReduction': {'corr_metric': 'spearman', 'threshold': 0.9,
                                                                    'cluster_reduction': 'medoid'},
                                        'classifier': classifier_options}
                    method = '_'.join(fs.split('_')[:-1])
                    n_features = None if fs.split('_')[-1] == 'None' else int(fs.split('_')[-1])
                    pipeline_dic = {'scale': StandardScaler,
                                    'DimensionalityReduction': 'hierarchical_clust_leger',
                                    'FeatureSelection': method, 'classifier': algo}
                    pipeline_options.update({'FeatureSelection': {'bootstrap': True, 'n_bsamples': 100,
                                                                  'n_selected_features': n_features,
                                                                  'ranking_aggregation': 'importance_score'}})
                models_dic.update({'%s_FS%s' % (dim_red, name_suffix): {'pipeline_dic': pipeline_dic,
                                                                        'params_dic': params_dic,
                                                                        'pipeline_options': pipeline_options}})
                if output == 'codeletion':
                    # ========= SMOTE ============
                    for model_name in models_dic:
                        models_dic[model_name]['pipeline_dic'] = {'scale': StandardScaler,
                                                                  'DimensionalityReduction': models_dic[model_name]['pipeline_dic']['DimensionalityReduction'],
                                                                  'FeatureSelection': models_dic[model_name]['pipeline_dic']['FeatureSelection'],
                                                                  'oversampling': imblearn.over_sampling.SMOTE,
                                                                  'classifier': models_dic[model_name]['pipeline_dic']['classifier']}
                        models_dic[model_name]['pipeline_options'].update({'oversampling': {'sampling_strategy': 'not majority', 'random_state': seed, 'k_neighbors': 3}})
    return models_dic


def get_dataset(dataset, dataset_dic, batch, harmonization='MComBat', covariates=None):
    if dataset == 'S+D':
        stat = pd.read_excel(dataset_dic['S'], index_col=0)
        dyn = pd.read_excel(dataset_dic['D'], index_col=0)
        if harmonization == 'MComBat':
            ref_batch = 'Vereos'
            stat = MComBat(stat, batch, ref_batch=ref_batch, save_dir=None, covariate=covariates)
        else:
            stat = ComBat(stat, batch, save_dir=None, covariate=covariates)
        feature_dic = {'S+D': pd.concat([stat, dyn], axis=1)}
    elif dataset == 'S+D+OR':
        stat = pd.read_excel(dataset_dic['S'], index_col=0)
        dyn = pd.read_excel(dataset_dic['D'], index_col=0)
        text = pd.read_excel(dataset_dic['OR'], index_col=0)
        stat_text = pd.concat([stat, text], axis=1)
        if harmonization == 'MComBat':
            ref_batch = 'Vereos'
            stat_text = MComBat(stat_text, batch, ref_batch=ref_batch, save_dir=None, covariate=covariates)
        else:
            stat_text = ComBat(stat_text, batch, save_dir=None, covariate=covariates)
        feature_dic = {'S+D+OR': pd.concat([stat_text, dyn], axis=1)}
    elif dataset == 'S':
        stat = pd.read_excel(dataset_dic['S'], index_col=0)
        if harmonization == 'MComBat':
            ref_batch = 'Vereos'
            stat = MComBat(stat, batch, ref_batch=ref_batch, save_dir=None, covariate=covariates)
        else:
            stat = ComBat(stat, batch, save_dir=None, covariate=covariates)
        feature_dic = {'S': stat}
    elif dataset == 'S+OR':
        stat = pd.read_excel(dataset_dic['S'], index_col=0)
        text = pd.read_excel(dataset_dic['OR'], index_col=0)
        stat_text = pd.concat([stat, text], axis=1)
        if harmonization == 'MComBat':
            ref_batch = 'Vereos'
            stat_text = MComBat(stat_text, batch, ref_batch=ref_batch, save_dir=None, covariate=covariates)
        else:
            stat_text = ComBat(stat_text, batch, save_dir=None, covariate=covariates)
        feature_dic = {'S+OR': stat_text}
    else:
        raise ValueError
    return feature_dic


def get_features_dic(dataset_list, dataset_dic, batch, harmonization='MComBat', covariates=None):
    if isinstance(dataset_list, str):
        dataset_list = [dataset_list]
    features_dic = {}
    for dataset in dataset_list:
        features_dic.update(get_dataset(dataset, dataset_dic, batch, harmonization, covariates))

    return features_dic


def statistical_pipeline(X, y, pipeline_dic, params_dic, pipeline_options,
                         save_dir=None, seed=111, n_jobs=None):
    start = time.time()
    # === NestedCV ===
    metric = 'roc_auc' if len(np.unique(y)) == 2 else 'roc_auc_ovo'
    outer_cv = RepeatedStratifiedKFold(n_splits=10, n_repeats=5, random_state=seed)
    inner_cv = RepeatedStratifiedKFold(n_splits=3, n_repeats=50, random_state=seed)
    refit_method = get_best_index
    clf = NestedCV(pipeline_dic, params_dic, outer_cv=outer_cv, inner_cv=inner_cv, n_jobs=n_jobs,
                   pipeline_options=pipeline_options,
                   metric=metric, verbose=2, refit_outer=False, return_train_score=True, imblearn_pipeline=True,
                   refit_inner=refit_method,
                   random_state=seed, randomized_search=True, randomized_search_iter=100)
    clf.fit(X, y)
    save_results(save_dir, clf, X, y, metric)
    print(time.time() - start)
    return clf


def get_best_index(cv_results):
    new_cv_results = cv_results
    best_rank_mask = new_cv_results['rank_test_score'] == new_cv_results['rank_test_score'].min()
    params_best_score = np.array(new_cv_results['params'])[best_rank_mask]
    params_name = params_best_score[0].keys()
    if 'FeatureSelection__n_selected_features' in params_name:
        fs_params = np.array([_['FeatureSelection__n_selected_features'] for _ in params_best_score])
        if None in fs_params:
            best_fs = None
        else:
            best_fs = fs_params.min()
        params_best_score = params_best_score[fs_params == best_fs]
    classifier_params_names = [_ for _ in params_name if 'classifier' in _]
    params_of_interest = ['classifier__C', 'classifier__base_estimator__C', 'classifier__max_features',
                          'classifier__alpha']
    param_chosen = [_ for _ in classifier_params_names if _ in params_of_interest]
    if param_chosen:
        classifier_params = np.array([_[param_chosen[0]] for _ in params_best_score])
        if params_of_interest != 'classifier__alpha':
            params_best_score = params_best_score[classifier_params == min(classifier_params)]
        else:
            params_best_score = params_best_score[classifier_params == max(classifier_params)]
    best_params = params_best_score[0]
    best_index = np.where(np.array(cv_results['params']) == best_params)[0][0]
    return best_index


if __name__ == '__main__':
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='AI pipeline')

    parser.add_argument("--root", "--r", metavar='/root/to/dataset/', help='Root directory of dataset',
                        default='./data')

    parser.add_argument("--covariates_file", "--covf", metavar='/path/to/covariate/file', help='path of the covariate file',
                        default='./data/clinical_data.xlsx')

    parser.add_argument("--output", "--o", metavar='Output studied', help='Name of output studied',
                        default='IDH')

    parser.add_argument("--threads", "--t", metavar='CPU jobs', help='Number of parallel threads',
                        default=-1, type=int)

    parser.add_argument("--save_root", "--save", metavar='/root/to/save/directory', help='Path of save root directory',
                        default='./data/results')

    args = parser.parse_args()

    start_time = time.time()

    output = args.output
    covariates = None
    harmonization = 'MComBat'
    dataset_dic = {'OR': os.path.join(os.path.abspath(args.root), 'OR_tumor.xlsx'),
                   'S': os.path.join(os.path.abspath(args.root), 'S.xlsx'),
                   'D': os.path.join(os.path.abspath(args.root), 'D.xlsx')}
    covariates_df = pd.read_excel(args.covariates_file, index_col=0)
    batch = covariates_df['Device']
    output_df = covariates_df[output]

    # === Find best model using S+D+OR ===
    feature_dic = get_features_dic('S+D+OR', dataset_dic, batch, harmonization, covariates)
    algo_list = ['LR_L2', 'SVC_linear', 'SVC_RBF', 'RF', 'Nnet']
    dr_list = ['Leger']
    fs_list = ['wlcx_score_5', 'wlcx_score_10', 'wlcx_score_15']
    models = get_models_randomsearch(output, algo_list, dr_list, fs_list)
    model_results = {}
    for model_name in models:
        print('\t\t\t================== %s %s %s ================' % (output, 'S+D+OR', model_name))
        save_dir = os.path.join(os.path.abspath(args.save_root), args.root, output, 'S+D+OR', model_name)
        os.makedirs(save_dir, exist_ok=True)
        feature_dic['S+D+OR'].to_csv(os.path.join(save_dir, '%s_harmonized_%s.csv' % ('S+D+OR', harmonization)))
        cv_results = statistical_pipeline(feature_dic['S+D+OR'], output_df,
                                          models[model_name]['pipeline_dic'],
                                          models[model_name]['params_dic'], models[model_name]['pipeline_options'],
                                          save_dir=save_dir, n_jobs=args.threads)
        model_results[model_name] = cv_results.outer_results

    print('Total Time: %.2f' % (time.time() - start_time))

    best_model_name = max(model_results, key=model_results.get)
    model_name_splitted = best_model_name.split('_')
    if len(model_name_splitted) == 5:
        dim_red = model_name_splitted[0]
        algo_name = model_name_splitted[-1]
        fs = '_'.join(model_name_splitted[1:-1]).split('FS')[-1]
    elif len(model_name_splitted) == 6:
        dim_red = model_name_splitted[0]
        algo_name = '_'.join(model_name_splitted[-2:])
        fs = '_'.join(model_name_splitted[1:-2]).split('FS')[-1]

    # === Get feature importance for best model ===
    best_model_dir = os.path.join(os.path.abspath(args.save_root), args.root, output, 'S+D+OR', best_model_name)
    pattern_feature = re.compile('FSwlcx_score_[a-zA-Z0-9]*_')
    # Get mean number of features selected by the model
    n_selected_metafeatures = re.findall(pattern_feature, best_model_name)[0][:-1].split('FS')[1].split('_')[-1]
    if n_selected_metafeatures == 'None':
        n_selected_metafeatures = None
    else:
        n_selected_metafeatures = int(n_selected_metafeatures)
    if n_selected_metafeatures is None:
        n_selected_features = n_selected_metafeatures
    else:
        pattern_fold = re.compile('outer[0-9]*f[0-9]*r')
        n_splits, n_repeats = 10, 5
        n_selected_features = []
        selected_features_df = pd.read_excel(os.path.join(best_model_dir, 'Selected_features.xlsx'), index_col=0,
                                             sheet_name=['outer%d' % (folds + 1) for folds in range(0, n_splits * n_repeats)])
        for fold in selected_features_df:
            metafeatures_selected = selected_features_df[fold][selected_features_df[fold]['Selected_features'] == True]
            metafeatures_selected = metafeatures_selected.drop('Selected_features', axis='columns')
            n_selected_features.append((metafeatures_selected != 0).sum(1).sum())
        n_selected_features = int(np.round(np.mean(n_selected_features)))
    pickle_file_list = natsorted([_ for _ in os.listdir(os.path.join(best_model_dir, 'Pickled_model')) if '.pkl' in _])
    # Get feature names
    df = pd.read_excel(os.path.join(best_model_dir, 'Selected_features.xlsx'), sheet_name='outer1', index_col=0)
    feature_names = df.columns[:-1]
    feature_names = [format_feature_name(_) for _ in feature_names]
    df.columns = feature_names + [df.columns[-1]]
    feature_importance_list = []
    shap_values_list = []
    for pickle_file in pickle_file_list:
        model_dic = load(os.path.join(best_model_dir, 'Pickled_model', pickle_file))
        outer_fold = 'outer%d' % (int(pickle_file.split('_')[-1].split('.')[0].split('outer')[-1]) + 1)
        print('%s / %d' % (outer_fold, len(pickle_file_list)))
        # Retrieve model information
        model = model_dic['Model']
        X_train, y_train, X_test, y_test = model_dic['X_train'], model_dic['y_train'], model_dic['X_test'], model_dic['y_test']
        # Create model explainer
        if isinstance(model, RandomForestClassifier):
            explainer = shap.TreeExplainer(model, feature_names=feature_names)
        elif isinstance(model, LogisticRegression):
            explainer = shap.LinearExplainer(model, X_train, feature_names=feature_names)
        else:
            if hasattr(model[-1], 'decision_function'):
                explainer = shap.KernelExplainer(model.decision_function, X_train)
            else:
                f = lambda x: model.predict_proba(x)[:, 1]
                explainer = shap.KernelExplainer(f, X_train)
        # Get SHAP values
        shap_values = explainer.shap_values(X_test, n_samples=100)
        shap_values_list.append(shap_values)
    global_shap_values = np.concatenate(shap_values_list)
    # Normalize shap so that sum is equal to 1
    mean_shap = np.abs(global_shap_values).mean(0)
    global_shap_values /= np.sum(mean_shap)
    feature_importance = mean_shap / np.sum(mean_shap)
    df = pd.DataFrame({'Feature': feature_names, 'Feature importance': feature_importance})
    df.to_excel(os.path.join(best_model_dir, 'Global_feature_importance.xlsx'))
    # Rank by importance
    feature_order = np.argsort(feature_importance)
    feature_order = feature_order[-n_selected_features:]

    f = plt.figure()
    shap.summary_plot(global_shap_values, feature_names=feature_names, plot_type='bar', show=False,
                      max_display=n_selected_features)
    ax = plt.gca()
    ax.set_xlabel('Feature importance')
    for index, value in enumerate(feature_importance[feature_order]):
        ax.text(value, index, ' %1.1f%%' % (value * 100), va='center')
    f.savefig(os.path.join(best_model_dir, 'Global_feature_importance.png'), bbox_inches='tight', dpi=600)
    plt.close(f)