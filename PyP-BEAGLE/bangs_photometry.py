import logging
import os
from scipy.integrate import simps, cumtrapz
from scipy.interpolate import interp1d
from bisect import bisect_left
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as plticker
#import pandas as pd
# SEABORN creates by default plots with a filled background!!
#import seaborn as sns
from astropy.io import ascii
from astropy.io import fits

import sys
sys.path.append("../dependencies")
import WeightedKDE

from bangs_utils import BangsDirectories, prepare_plot_saving, set_plot_ticks, \
        prepare_violin_plot, plot_exists, pause, extract_row
from bangs_filters import PhotometricFilters
from bangs_summary_catalogue import BangsSummaryCatalogue
from bangs_residual_photometry import ResidualPhotometry
from bangs_multinest_catalogue import MultiNestCatalogue
from bangs_posterior_predictive_checks import PosteriorPredictiveChecks


Jy = np.float32(1.E-23)
microJy = np.float32(1.E-23 * 1.E-06)
nanoJy = np.float32(1.E-23 * 1.E-09)

p_value_lim = 0.05



class ObservedCatalogue:

    def load(self, file_name):

        """ 
        Load a photometric catalogue of observed sources. It automatically
        detects, and loads, FITS or ASCII files depending on the suffix.

        Parameters
        ----------
        file_name : str
            Contains the file name of the catalogue.
        """

        if file_name.endswith(('fits', 'fit', 'FITS', 'FIT')):
            self.data = fits.open(file_name)[1].data
            self.columns = fits.open(file_name)[1].columns
        else:
            self.data = ascii.read(file_name, Reader=ascii.basic.CommentedHeader)


    def extract_fluxes(self, filters, ID, aper_corr=1.):
        """ 
        Extract fluxes and error fluxes for a single object (units are Jy).

        Parameters
        ----------
        filters : class
            Contains the photometric filters

        ID : int, str
            Contains the object ID

        Returns    
        -------
        flux : array
            In units of Jy

        flux_error : array 
            In units of Jy

        Notes
        -----
        The routine also adds in quadrature the minimum relative error defined int he filters class.

        """

        flux = np.zeros(filters.n_bands, np.float32)
        flux_err = np.zeros(filters.n_bands, np.float32)

        row = extract_row(self.data, ID)

        for j in range(filters.n_bands):

            # observed flux and its error
            name = filters.data['flux_colName'][j]
            flux[j] = row[name] * aper_corr * filters.units / Jy

            name = filters.data['flux_errcolName'][j]
            flux_err[j] = row[name] * aper_corr * filters.units / Jy

            if flux_err[j] > 0.:
                # if defined, add the minimum error in quadrature
                flux_err[j] = (np.sqrt((flux_err[j]/flux[j])**2 +
                    np.float32(filters.data['min_rel_err'][j])**2) *
                    flux[j])

        return flux, flux_err

class Photometry:

    def __init__(self):

        self.filters = PhotometricFilters()

        self.observed_catalogue = ObservedCatalogue()

        self.summary_catalogue = BangsSummaryCatalogue()

        self.multinest_catalogue = MultiNestCatalogue()

        self.residual = ResidualPhotometry()

        self.PPC = PosteriorPredictiveChecks()

    def plot_marginal(self, ID, max_interval=99.7, 
            print_text=False, print_title=False, replot=False, show=False, units='nanoJy'):    
        """ 
        Plot the fluxes predicted by BANGS.

        The fluxes here considered are those predicted by BANGS, given the
        posterior distribution of the model parameters. These are *not*
        replicated data.

        Parameters
        ----------
        ID : int
            ID of the galaxy whose marginal photometry will be plotted.

        max_interval : float, optional
            The marginal photometry is shown to include `max_interval`
            probability, e.g. `max_interval` = 68. will show the 68 % (i.e.
            '1-sigma') (central) credible region of the marginal photometry.

        print_text : bool, optional
            Whether to print further information on the plot, such as
            chi-square, p-value, or leave it empty and neat.

        print_text : bool, optional
            Whether to print the object ID on the top of the plot.

        replot: bool, optional
            Whether to redo the plot, even if it already exists
        """

        # Name of the output plot
        plot_name = str(ID)+'_BANGS_marginal_SED_phot.pdf'

        # Check if the plot already exists
        if plot_exists(plot_name) and not replot and not show:
            logging.warning('The plot "' + plot_name + '" already exists. \n Exiting the function.')
            return

        # From the (previously loaded) observed catalogue select the row
        # corresponding to the input ID
        observation = extract_row(self.observed_catalogue.data, ID)

        # Check if you need to apply an aperture correction to the catalogue fluxes
        if 'aper_corr' in self.observed_catalogue.data.dtype.names:
            aper_corr = 10.**(-0.4*observation[0]['aper_corr'])
        else:
            aper_corr = 1.

        # Put observed photometry and its error in arrays
        obs_flux, obs_flux_err = self.observed_catalogue.extract_fluxes(self.filters, ID)
        obs_flux *= 1.E+09
        obs_flux_err *= 1.E+09

        ok = np.where(obs_flux_err > 0.)[0]

        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1)

        # Open the file containing BANGS results
        fits_file = os.path.join(BangsDirectories.results_dir, str(ID)+'_BANGS.fits.gz')
        hdulist = fits.open(fits_file)

        # Consider only the extension containing the predicted model fluxes
        model_sed = hdulist['marginal photometry']
        probability = hdulist['posterior pdf'].data['probability']

        n_bands = len(model_sed.columns.names)
        median_flux = np.zeros(n_bands)
        pdf_norm = np.zeros(n_bands)
        _max_y = np.zeros(n_bands)
        min_flux = np.zeros(n_bands)
        max_flux = np.zeros(n_bands)

        y_plot = list(range(n_bands))
        x_plot = list(range(n_bands))

        has_pdf = list(range(n_bands))
        kde_pdf = list(range(n_bands))
        nXgrid = 1000

        width = 5*np.min(np.array(self.filters.data['wl_eff'][1:])-np.array(self.filters.data['wl_eff'][0:-1]))

        kwargs = {'color':'tomato', 'alpha':0.7, 'edgecolor':'black', 'linewidth':0.2}

        for i in range(n_bands):
            xdata = model_sed.data.field(i) / nanoJy

            min_x = np.min(xdata)
            max_x = np.max(xdata)

            min_flux[i] = min_x
            max_flux[i] = max_x

            # if min_x == max_x, then you can not use weighted KDE, since you
            # just have one value for the x...this usually happens bacause of
            # IGM absorption, which absorbs the flux blue-ward 1216 AA, making
            # all flux = 0
            if min_x == max_x:
                has_pdf[i] = False
                median_flux[i] = min_x
                continue

            # Compute the marginal PDF through a weighted KDE
            has_pdf[i] = True

            # This function provides you with all the necessary info to draw violin plots
            kde_pdf[i], pdf_norm[i], median_flux[i], x_plot[i], y_plot[i] = prepare_violin_plot(xdata, weights=probability) 

            _max_y[i] = np.max(y_plot[i])

        delta_wl = np.array(self.filters.data['wl_eff'][1:])-np.array(self.filters.data['wl_eff'][0:-1])
        delta_wl = np.concatenate(([delta_wl[0]], delta_wl))

        for i in range(n_bands):

            dwl = delta_wl[i]
            if i > 1:
                dwl = np.min(delta_wl[i-1:i])

            if has_pdf[i]:

                w = 0.4 * dwl / _max_y[i]

                y_grid = np.full(len(x_plot[i]), self.filters.data['wl_eff'][i])
                _lim_y = kde_pdf[i](median_flux[i])/pdf_norm[i] * w

                ax.fill_betweenx(x_plot[i],
                        y_grid - y_plot[i]*w,
                        y_grid + y_plot[i]*w,
                        **kwargs
                        )

                ax.plot( [self.filters.data['wl_eff'][i]-_lim_y, self.filters.data['wl_eff'][i]+_lim_y],
                        [median_flux[i], median_flux[i]],
                        color = 'black',
                        linewidth = 0.2
                        )

            ax.plot( self.filters.data['wl_eff'][i],
                    median_flux[i],
                    color = 'black',
                    marker = "o",
                    markersize = 5,
                    alpha = 0.7
                    )


        # Determine min and max values of y-axis
        yMax = np.max(max_flux)
        yMin = np.min(np.concatenate((obs_flux[ok]-obs_flux_err[ok], min_flux)))

        dY = yMax-yMin

        yMax += dY * 0.1
        yMin -= dY * 0.1

        ax.set_ylim([yMin, yMax])

        x0 = self.filters.data['wl_eff'][0]
        x1 = self.filters.data['wl_eff'][-1]
        dx = x1-x0
        ax.set_xlim([x0-0.05*dx, x1+0.05*dx])

        x0, x1 = ax.get_xlim()
        if yMin < 0.: plt.plot( [x0,x1], [0.,0.], color='gray', lw=0.8 )

        # Define plotting styles
        ax.set_xlabel("$\lambda_\\textnormal{eff} / \\textnormal{\AA}$ (observed-frame)")
        ax.set_ylabel("$f_{\\nu}/\\textnormal{nanoJy}$")

        # Set better location of tick marks
        set_plot_ticks(ax, n_x=5)

        kwargs = {'alpha':0.8}

        plt.errorbar(self.filters.data['wl_eff'], 
                obs_flux, 
                yerr = obs_flux_err,
                color = "dodgerblue",
                ls = " ",
                marker = "D",
                markeredgewidth = 0.,
                markersize = 8,
                elinewidth=1.0,
                capsize=3,
                **kwargs)


        # Title of the plot is the object ID
        if print_title: plt.title(str(ID))

        # Location of printed text
        x0, x1 = ax.get_xlim()
        x = x0 + (x1-x0)*0.03
        y0, y1 = ax.get_ylim()
        y = y1 - (y1-y0)*0.10

        if print_text:

            # Print the evidence
            try:
                ax.text(x, y, "$\log(Z)=" + "{:.2f}".format(self.logEvidence) + "$", fontsize=10 )
            except AttributeError:
                print "ciao"

            # Print the average reduced chi-square
            try:
                row = extract_row(self.PPC.data, ID)
                aver_chi_square = row['aver_chi_square']
                y = y1 - (y1-y0)*0.15
                ax.text(x, y, "$\langle\chi^2\\rangle=" + "{:.2f}".format(aver_chi_square) + "$", fontsize=10 )
            except AttributeError:
                print "`PosteriorPredictiveChecks` not computed/loaded, hence " \
                "<chi^2> for the object `" + str(ID) + "` is not available"

            try:
                row = extract_row(self.PPC.data, ID)
                aver_red_chi_square = row['aver_red_chi_square']
                n_data = row['n_used_bands']
                y = y1 - (y1-y0)*0.20
                ax.text(x, y,
                        "$\langle\chi^2/(\\textnormal{N}_\\textnormal{data}-1)\\rangle=" \
                        + "{:.2f}".format(aver_red_chi_square) + "\; \
                        (\\textnormal{N}_\\textnormal{data}=" + \
                        "{:d}".format(n_data) + ")" + "$", fontsize=10 )
            except AttributeError:
                print "`PosteriorPredictiveChecks` not computed/loaded, hence " \
                "<chi^2_red> for the object `" + str(ID) + "` is not available"

        if y0 < 0.: plt.plot( [x0,x1], [0.,0.], color='gray', lw=1.0 )

        if show:
            plt.show()
        else:
            name = prepare_plot_saving(plot_name)

            fig.savefig(name, dpi=None, facecolor='w', edgecolor='w',
                    orientation='portrait', papertype='a4', format="pdf",
                    transparent=False, bbox_inches="tight", pad_inches=0.1)

        plt.close(fig)

        hdulist.close()

    def plot_replicated_data(self, ID, max_interval=99.7, n_replic_to_plot=16,
            print_text=False, replot=False):    
        """ 
        Plot the replicated data.

        Parameters
        ----------
        ID : int
            ID of the galaxy whose marginal photometry will be plotted.

        max_interval : float, optional
            The marginal photometry is shown to include `max_interval`
            probability, e.g. `max_interval` = 68. will show the 68 % (i.e.
            '1-sigma') (central) credible region of the marginal photometry.

        n_replic_to_plot: int, optional
            The number of replicated data that will be plotted. It can be given
            as a single number, or as a pair (n_x, n_y), in which case the
            total number of replicated data plotted will be n_x * n_y

        print_text : bool, optional
            Whether to print further information on the plot, such as
            chi-square, p-value, or leave it empty and neat.

        replot: bool, optional
            Whether to redo the plot, even if it already exists
        """

        # Name of the output plot
        plot_name = str(ID)+'_BANGS_replic_data_phot.pdf'

        # Check if the plot already exists
        if plot_exists(plot_name) and not replot:
            logging.warning('The plot "' + plot_name + '" already exists. \n Exiting the function.')
            return

        n_replic_to_plot = np.array(n_replic_to_plot)
        if n_replic_to_plot.size == 1:
            n_plot_x = int(np.sqrt(n_replic_to_plot))
            n_plot_y = n_plot_x
        else:
            n_plot_x = n_replic_to_plot[0]
            n_plot_y = n_replic_to_plot[1]

        # From the (previously loaded) observed catalogue select the row
        # corresponding to the input ID
        observation = extract_row(self.observed_catalogue.data, ID)

        # Check if you need to apply an aperture correction to the catalogue fluxes
        if 'aper_corr' in self.observed_catalogue.data.dtype.names:
            aper_corr = 10.**(-0.4*observation[0]['aper_corr'])
        else:
            aper_corr = 1.

        # Put observed photometry and its error in arrays
        obs_flux, obs_flux_err = self.observed_catalogue.extract_fluxes(self.filters, ID)
        obs_flux *= 1.E+09
        obs_flux_err *= 1.E+09

        ok = np.where(obs_flux_err > 0.)[0]

        # Open the file containing BANGS results
        fits_file = os.path.join(BangsDirectories.results_dir,
                str(ID)+'_BANGS.fits.gz')

        model_hdu = fits.open(fits_file)
        model_sed = model_hdu['marginal photometry']

        # Open the file containing the replicated data
        fits_file = os.path.join(BangsDirectories.results_dir,
                BangsDirectories.pybangs_data,
                str(ID)+'_BANGS_replic_data.fits.gz')

        replic_hdu = fits.open(fits_file)
        replic_data = replic_hdu[1]

        n_replicated = replic_data.data.field(0).size

        # the first column is the ID, so the number of bands is n-1
        n_bands = len(replic_data.columns.names)-1

        indices = replic_data.data['row_index']
        noiseless_flux = np.zeros((n_bands, indices.size))
        for i in range(n_bands):
            noiseless_flux[i, :] = model_sed.data[indices].field(i) / nanoJy

        # Consider only those bands with measurements!
        ok_bands = np.where(obs_flux_err > 0.)[0]

        # Compute the p-value band-by-band
        p_value_bands = np.zeros(n_bands)
        replic_fluxes = np.zeros((n_bands, n_replicated))
        for i in range(n_bands):
            
            if obs_flux_err[i] > 0.:
                obs_discr = (obs_flux[i].repeat(n_replicated)-noiseless_flux[i, :])**2 / obs_flux_err[i].repeat(n_replicated)**2
                repl_discr = (replic_data.data.field(i+1)/1.E-09-noiseless_flux[i, :])**2 / obs_flux_err[i].repeat(n_replicated)**2

                replic_fluxes[i,:] = replic_data.data.field(i+1)/1.E-09
                p_value_bands[i] = 1. * np.count_nonzero((repl_discr >
                    obs_discr)) / n_replicated

        markers = np.array("o").repeat(n_bands)
        loc = np.where(p_value_bands <= p_value_lim)[0]
        markers[loc] = "o"
        print "p_value_bands: ", p_value_bands

        ext_obs_flux = obs_flux.reshape(n_bands, 1).repeat(n_replicated, 1)
        ext_obs_flux_err = obs_flux_err.reshape(n_bands, 1).repeat(n_replicated, 1)

        obs_discr = np.sum((ext_obs_flux[ok_bands,:]-noiseless_flux[ok_bands,:])**2 / ext_obs_flux_err[ok_bands,:]**2, axis=0)
        repl_discr = np.sum((replic_fluxes[ok_bands,:]-noiseless_flux[ok_bands,:])**2 / ext_obs_flux_err[ok_bands,:]**2, axis=0)

        p_value = 1. * np.count_nonzero((repl_discr >
            obs_discr)) / n_replicated
        
        print "p_value: ", p_value

        median_flux = np.zeros(n_bands)
        pdf_norm = np.zeros(n_bands)
        _max_y = np.zeros(n_bands)
        max_abs_flux = np.zeros(n_plot_x*n_plot_y)

        # Compute mean residual
        replic_fluxes = np.zeros((n_bands, n_replicated))
        mean_replic_fluxes = np.zeros(n_bands)
        for i in range(n_bands):
            mean_replic_fluxes[i] = np.mean(replic_data.data.field(i+1))/1.E-09
            replic_fluxes[i,:] = replic_data.data.field(i+1)/1.E-09

        mean_residual = (mean_replic_fluxes-obs_flux)/obs_flux_err 

        # Compute variance-covariance matrix of residual
        residual_fluxes = (replic_fluxes-obs_flux.reshape(n_bands,
            1).repeat(n_replicated, 1)) / obs_flux_err.reshape(n_bands,
                    1).repeat(n_replicated, 1)

        residual_covar = np.cov(residual_fluxes)
        print "residual_covar: ", residual_covar

        # Plot the variance-covariance matrix of residuals
        if 'sns' in sys.modules:

            sns.set(style="white")
            labels = list()
            for lab in self.filters.data['label']:
                labels.append(lab.split('_')[-1])
                
            #d = pd.DataFrame(data=np.abs(residual_fluxes.T),
            #        columns=labels)

            # Compute the correlation matrix
            #corr = d.corr()

            # Generate a mask for the upper triangle
            mask = np.zeros_like(corr, dtype=np.bool)
            mask[np.triu_indices_from(mask)] = True

            # Set up the matplotlib figure
            fig, ax = plt.subplots()

            # Generate a custom diverging colormap
            cmap = sns.diverging_palette(220, 10, as_cmap=True)

            # Draw the heatmap with the mask and correct aspect ratio
            sns.heatmap(corr, mask=mask, cmap=cmap, vmax=0.4,
                    square=True, annot=True, fmt=".2f", annot_kws={"size": 10},
                    linewidths=.5, cbar_kws={"shrink": .85}, ax=ax)

            # Rotate by 45 deg the x and y ticks so they do not overlap
            plt.setp( ax.xaxis.get_majorticklabels(), rotation=45,
                    horizontalalignment='right' )

            plt.setp( ax.yaxis.get_majorticklabels(), rotation=45,
                    horizontalalignment='right' )

            name = prepare_plot_saving(str(ID)+'_BANGS_replic_data_phot_matrix.pdf')

            fig.savefig(name, dpi=None, facecolor='w', edgecolor='w',
                    orientation='portrait', papertype='a4', format="pdf",
                    transparent=False, bbox_inches="tight", pad_inches=0.1)

            fig.clear()
            plt.close(fig)

        # Select a random set of repliated data
        np.random.seed(seed=12345678)
        replic_data_rows = np.random.choice(n_replicated, size=n_plot_x*n_plot_y)    

        # Now plot the replicated data, along with the data, in different subplots
        wl_eff = self.filters.data['wl_eff']

        fig, axs = plt.subplots(n_plot_x, n_plot_y, sharex=True, sharey=True)
        fig.subplots_adjust(left=0.08, bottom=0.08, hspace=0, wspace=0)
        fontsize = 8
        axes_linewidth = 0.7

        ix = 0
        iy = 0
        for i, ax in enumerate(np.ravel(axs)):


#            kwargs = {'alpha':0.7}
#            (_, caps, _) = ax.errorbar(wl_eff, 
#                    obs_flux, 
#                    yerr=obs_flux_err, 
#                    ls=' ', 
#                    marker='o', 
#                    markersize=5, 
#                    color='orangered',
#                    markeredgewidth = 0.,
#                    elinewidth=1.0,
#                    capsize=2,
#                    **kwargs)
#
#            for cap in caps:
#                cap.set_color('orangered')
#                cap.set_markeredgewidth(1)
#
            temp_data = replic_data.data[replic_data_rows[i]]
            replic_fluxes = np.array(temp_data[1:])/1.E-09

            diff_fluxes = (replic_fluxes-obs_flux) / obs_flux_err

            kwargs = {'alpha':0.4}
            unique_markers = np.unique(markers)
            if i != 0:
                for um in unique_markers:
                    mask = markers == um 

                    (_, caps, _) = ax.errorbar(wl_eff[mask], 
                            diff_fluxes[mask], 
                            ls=' ', 
                            marker=um, 
                            markersize=5, 
                            color='black',
                            markeredgewidth = 0.,
                            elinewidth=1.0,
                            capsize=2,
                            **kwargs)

                    for cap in caps:
                        cap.set_color('black')
                        cap.set_markeredgewidth(1)

            if i == 0:

                nXgrid = 1000
                kwargs = {'alpha':0.7}

                delta_wl = np.array(self.filters.data['wl_eff'][1:])-np.array(self.filters.data['wl_eff'][0:-1])
                delta_wl = np.concatenate(([delta_wl[0]], delta_wl))

                for j in range(n_bands):

                    residual = residual_fluxes[j,:]

                    # This function provides you with all the necessary info to draw violin plots
                    kde_pdf, pdf_norm, median_flux, x_plot, y_plot = prepare_violin_plot(residual)

                    w = 0.4 * delta_wl[j] / np.max(y_plot)

                    y_grid = np.full(len(x_plot), self.filters.data['wl_eff'][j])

                    _lim_y = kde_pdf(median_flux)/pdf_norm * w

                    ax.fill_betweenx(x_plot,
                            y_grid - y_plot*w,
                            y_grid + y_plot*w,
                            **kwargs
                            )

                    ax.plot( [self.filters.data['wl_eff'][j]-_lim_y, self.filters.data['wl_eff'][j]+_lim_y],
                            [median_flux, median_flux],
                            color = 'black',
                            linewidth = 0.2
                            )

            #min_flux[i] = np.min(np.array([replic_fluxes-obs_flux_err, obs_flux-obs_flux_err]))
            #max_flux[i] = np.max(np.array([replic_fluxes+obs_flux_err, obs_flux+obs_flux_err]))
            max_abs_flux[i] = np.max(np.abs(diff_fluxes))

        # Determine min and max values of y-axis
        yMax = np.max(max_abs_flux)
        yMin = -yMax
        dY = yMax-yMin
        yMax += dY * 0.1
        yMin -= dY * 0.1

        xMin = self.filters.data['wl_eff'][0]
        xMax = self.filters.data['wl_eff'][-1]
        dX = xMax-xMin
        xMax += dX * 0.1
        xMin -= dX * 0.1

        for ax in np.ravel(axs):        
            ax.set_ylim([yMin, yMax])
            ax.set_xlim([xMin, xMax])

            ax.tick_params(which='minor', axis='both',
                            length=2, width=axes_linewidth)

            ax.tick_params(which='major', axis='both',
                            length=3.5, width=axes_linewidth)

            x0, x1 = ax.get_xlim()
            if yMin < 0.: ax.plot( [x0,x1], [0.,0.], color='gray', lw=0.8 )


            for item in ([ax.title, ax.xaxis.label, ax.yaxis.label] +
                         ax.get_xticklabels() + ax.get_yticklabels()):
                item.set_fontsize(fontsize)


            for axis in ['top','bottom','left','right']:
              ax.spines[axis].set_linewidth(axes_linewidth)

            # Set better location of tick marks
            set_plot_ticks(ax, n_x=4, prune_x='both', prune_y='both')

        xlabel = "$\lambda_\\textnormal{eff} / \\textnormal{\AA}$ (observed-frame)"
        #ylabel = "$f_{\\nu}/\\textnormal{nanoJy}$"
        ylabel = "$\left(f_{\\nu}^\\textnormal{rep}-f_{\\nu}\\right) / \sigma$"

        fig.text(0.5, 0.02, xlabel, ha='center', fontsize=fontsize+1)
        fig.text(0.03, 0.5, ylabel, va='center', rotation='vertical', fontsize=fontsize+1)

        # Title of the plot is the object ID
        #plt.title(str(ID))

        # Location of printed text
        #x0, x1 = ax.get_xlim()
        #x = x0 + (x1-x0)*0.03
        #y0, y1 = ax.get_ylim()
        #y = y1 - (y1-y0)*0.10

        if print_text:

            # Print the evidence
            try:
                ax.text(x, y, "$\log(Z)=" + "{:.2f}".format(self.logEvidence) + "$", fontsize=10 )
            except AttributeError:
                print "ciao"

            # Print the average reduced chi-square
            try:
                row = extract_row(self.PPC.data, ID)
                aver_chi_square = row['aver_chi_square']
                y = y1 - (y1-y0)*0.15
                ax.text(x, y, "$\langle\chi^2\\rangle=" + "{:.2f}".format(aver_chi_square) + "$", fontsize=10 )
            except AttributeError:
                print "`PosteriorPredictiveChecks` not computed/loaded, hence " \
                "<chi^2> for the object `" + str(ID) + "` is not available"

            try:
                row = extract_row(self.PPC.data, ID)
                aver_red_chi_square = row['aver_red_chi_square']
                n_data = row['n_used_bands']
                y = y1 - (y1-y0)*0.20
                ax.text(x, y,
                        "$\langle\chi^2/(\\textnormal{N}_\\textnormal{data}-1)\\rangle=" \
                        + "{:.2f}".format(aver_red_chi_square) + "\; \
                        (\\textnormal{N}_\\textnormal{data}=" + \
                        "{:d}".format(n_data) + ")" + "$", fontsize=10 )
            except AttributeError:
                print "`PosteriorPredictiveChecks` not computed/loaded, hence " \
                "<chi^2_red> for the object `" + str(ID) + "` is not available"


        #fig.tight_layout()

        name = prepare_plot_saving(plot_name)

        fig.savefig(name, dpi=None, facecolor='w', edgecolor='w',
                orientation='portrait', papertype='a4', format="pdf",
                transparent=False, bbox_inches="tight", pad_inches=0.1)

        plt.close(fig)

        model_hdu.close()
        replic_hdu.close()

##    def plot_residuals(self, residual_file_name=None, residual_plotname=None):
##
##        if not hasattr(self, 'observed_catalogue'):
##            except AttributeError:
##                    "An observed catalogue must be loaded before plotting the
##                    residual"
##
##        if not hasattr(self, 'bangs_summary_catalogue'):
##            except AttributeError:
##                    "A `bangs_summary_catalogue` must be loaded before plotting the
##                    residual"
##
##        self.residual = ResidualPhotometry()
##
##        try:
##            self.residual.load(self.residual_file_name)
##        except:
##            self.residual.compute(self.observed_catalogue,
##                self.bangs_summary_catalogue, self.self.filters.
##                cPickleName=self.residual_file_name)
                

