import numpy as np
import trident
import yt
import os
import sys
from multiprocessing import Pool,current_process,cpu_count
import itertools
import logging

import parse_vela_metadata
from sys import platform as _platform

yt.funcs.mylog.setLevel(50)

def convert_to_xyz(r, theta, phi):
    return np.array([r*np.sin(theta)*np.cos(phi),r*np.sin(theta)*np.sin(phi),r*np.cos(theta)])


def rotation_matrix(axis, theta):
    """
    Return the rotation matrix associated with counterclockwise rotation about
    the given axis by theta radians.
    """
    axis = np.asarray(axis)
    axis = axis/np.sqrt(np.dot(axis, axis))
    a = np.cos(theta/2.0)
    b, c, d = -axis*np.sin(theta/2.0)
    aa, bb, cc, dd = a*a, b*b, c*c, d*d
    bc, ad, ac, ab, bd, cd = b*c, a*d, a*c, a*b, b*d, c*d
    return np.array([[aa+bb-cc-dd, 2*(bc+ad), 2*(bd-ac)],
                     [2*(bc-ad), aa+cc-bb-dd, 2*(cd+ab)],
                     [2*(bd+ac), 2*(cd-ab), aa+dd-bb-cc]])

def get_rotation_matrix(L):
    zhat = np.array([0,0,1])
    theta = np.arccos(L[2]/np.linalg.norm(L))
    axis = np.cross(zhat,L)
    return rotation_matrix(axis,theta)
        

def ray_endpoints_spherical(R,r,theta,phi,alpha,endonsph):
    start = convert_to_xyz(R,theta,phi)
    xhat = convert_to_xyz(1,np.pi/2,np.pi/2+phi)
    yhat = convert_to_xyz(1,np.pi/2-theta,np.pi+phi)
    mid = r*(np.cos(alpha)*xhat+np.sin(alpha)*yhat)
    diff = start-mid
    if endonsph:
        t = 2*np.dot(start,diff)/np.dot(diff,diff)
    else:
        t = 2*R/np.linalg.norm(diff)
    end = start*(1-t)+mid*t
    return np.array([start,end])

def weights(array,function):
    if function == "sin":
        probs = np.sin(array)/2
        probs[0] = probs[-1]
    elif function == "lin":
        probs = np.linspace(0,1,len(array)+1)[1:]
    probs /= np.sum(probs)
    return probs

def ions_to_field_name(ions):
    lst = []
    for ion in ions:
        lst += [('gas',ion_to_field_name(ion))]
    return lst

def ion_to_field_name(ion):
    atom = ion.split(" ")[0]
    ionization = trident.roman.from_roman(ion.split(" ")[1])-1
    return "%s_p%s_number_density"%(atom,ionization)

class QuasarSphere(object):
    def __init__(self,ions=None,sim_name=None,dspath=None,data = None,\
                 simparams = None,scanparams = None,Rvir = None,L = None):
        if simparams == None:
            #need to load simulation from filename
            if dspath:
                self.ds = yt.load(dspath)
                z = self.ds.current_redshift
                c = self.ds.find_max("density")[1].value
            else:
                #for testing without loading real sim
                self.ds = None
                z = -1.0
                c = np.zeros(3)
            if L == None:
                L = np.array([0,0,1])
            self.simparams = [None]*7
            self.simparams[0] = sim_name
            self.simparams[1] = z
            self.simparams[2] = c[0]
            self.simparams[3] = c[1]
            self.simparams[4] = c[2]
            self.simparams[5] = Rvir
            self.simparams[6] = dspath
            self.simparams[7] = L[0]
            self.simparams[8] = L[1]
            self.simparams[9] = L[2]
        else:
            self.simparams = simparams
            self.ds = yt.load(simparams[6])
        if type(ions) is list:
            self.ions = ions
        elif type(ions) is str:
            self.ions = ions[1:-1].split(", ")
        else:
            self.ions = []
        self.scanparams = scanparams
        self.info = data

    def create_QSO_endpoints(self, R, n_th, n_phi, n_r, rmax, length,\
                             distances = "kpc", overwrite = False, endonsph = False):
        if not overwrite and self.scanparams:
            print "overwrite is FALSE, set to TRUE to create new scan."
            return None
        r_arr = np.linspace(0,rmax,n_r)
        th_arr = np.linspace(0,np.pi,n_th,endpoint = False)
        phi_arr = np.linspace(0,2*np.pi,n_phi,endpoint = False)
        if distances == "kpc":
            convert = self.ds.length_unit.in_units('kpc').value
        elif distances == "Rvir":
            convert = self.ds.length_unit.in_units('kpc').value
            convert /= self.simparams[5]
        else:
            convert = 1
        R /= convert
        r_arr /= convert
        self.scanparams = [None]*7
        self.scanparams[0] = R
        self.scanparams[1] = len(th_arr)
        self.scanparams[2] = len(phi_arr)
        self.scanparams[3] = len(r_arr)
        self.scanparams[4] = rmax
        self.scanparams[5] = length
        self.scanparams[6] = 0
        
        self.info = np.zeros((int(length),11+len(self.ions)+1))-1.0
        weightth = weights(th_arr, "sin")
        weightr = weights(r_arr, "lin")
        L = np.simparams[7:10]
        rot_matrix = get_rotation_matrix(L)
        for i in range(int(length)):
            theta = np.random.choice(th_arr,p = weightth)
            r = np.random.choice(r_arr,p = weightr)
            phi= np.random.choice(phi_arr)
            alpha = 2*np.pi*np.random.random()
            self.info[i][:5] = np.array([i,theta,phi,r,alpha])
            self.info[i][5:8] = np.matmul(rot_matrix, ray_endpoints_spherical(R,r,theta,phi,alpha,endonsph)[0]) + self.simparams[2:5]
            self.info[i][8:11] = np.matmul(rot_matrix, ray_endpoints_spherical(R,r,theta,phi,alpha,endonsph)[1]) + self.simparams[2:5] 
        print(str(length)+" LOSs to scan.")
        return length

    def get_coldens(self, save = 10, parallel = False):
        tosave = save
        starting_point = self.scanparams[6]
        if not parallel:
            for vector in self.info[starting_point:]:
                self.scanparams[6]+=1
                print("%s/%s"%(self.scanparams[6],self.scanparams[5]))
                vector = _get_coldens_helper((self.ds,self.scanparams,vector,self.ions))
                tosave -= 1
                if tosave == 0:
                    output = self.save_values()
                    print("file saved to "+output+".")
                    tosave = save
        if parallel:
            bins = np.append(np.arange(0,self.scanparams[5],save),self.scanparams[5])
            pool = Pool(processes = save,maxtasksperchild = 3)
            for i in range(0, len(bins)-1):
                current_info = self.info[bins[i]:bins[i+1]]
                if current_info[-1,0] < starting_point:
                    continue
                print("%s-%s /%s"%(bins[i],bins[i+1],len(self.info)))
                new_info = pool.map(_get_coldens_helper,itertools.izip(itertools.repeat(self.ds),itertools.repeat(self.scanparams),current_info, itertools.repeat(self.ions)))
                self.info[bins[i]:bins[i+1]] = new_info
                self.scanparams[6]+=save
                output = self.save_values()
                print("file saved to "+output+".")
        output = self.save_values()
        print("file saved to "+output+".")
        return self.info
    
    def save_values(self,dest = None):
        if len(self.info[0]) <= 11:
            print("No ions!")
        linesfinished = self.scanparams[6]
        numlines = self.scanparams[5]
        redshift = self.simparams[1]
        simname = self.simparams[0]
        ionsstr = ""
        for ion in self.ions:
            ionsstr += "_"+ion.replace(" ","")
        if dest:
            filename = dest
        else:
            foldername = "output/"+simname+"coldensinfo"
            if not os.path.exists(foldername):
                os.makedirs(foldername)
            specificfilename = "%s_of_%s-"%(str(linesfinished),str(numlines)) +ionsstr+"_z"+str(redshift)[:4]+".txt"
            filename = foldername+"/"+specificfilename
            prev = os.listdir(foldername)
            for item in prev:
                if item.endswith("of_%s-"%str(numlines) +ionsstr+"_z"+str(redshift)[:4]+".txt"):
                    os.remove(foldername+"/"+item)
        f = file(filename,"w+")
        firstline = "[dsname, z, center[0], center[1], center[2], Rvir, pathname]\n"
        secondline = str(self.simparams)+"\n"
        thirdline = "[R, n_th, n_phi, n_r, r_max, num_lines, line_reached]\n"
        fourthline = str(self.scanparams)+"\n"
        fifthline = "ions\n"
        sixthline = "["+str(self.ions[0])
        for ion in self.ions[1:]:
            sixthline += ", "+ion
        f.write(firstline)
        f.write(secondline)
        f.write(thirdline)
        f.write(fourthline)
        f.write(fifthline)
        f.write(sixthline+"]\n")
        for vector in self.info:
            f.write(str(vector).replace("\n",""))
            f.write("\n")
        f.close()
        return filename
    
    def plot_hist(self,simname = None,xvariable = "r",zeros = "ignore",weights = True,save_fig = None,ns = (42,15)):
        if not simname:
            simname = self.simparams[0]
        if xvariable == "r":
            conversion = self.ds.length_unit.in_units('kpc').value
        else:
            conversion = 1
        vardict = {"theta":1,"phi":2,"r":3}
        #ion,xvars,cdens,simname
        for i in range(len(self.ions)):
            end = self.scanparams[6]
            plot2dhist(self.ions[i],self.info[:end,vardict[xvariable]]*conversion,\
                       self.info[:end,11+i],simname,xvar = xvariable, ns = ns,zeros = zeros,\
                       weights = weights,save_fig = save_fig)

def read_values(filename):
    """ firstline = "[dsname, z, center[0], center[1], center[2], Rvir, pathname]\n"
        secondline = str(self.simparams)+"\n"
        thirdline = "[R, n_th, n_phi, n_r, r_max, num_lines, line_reached]\n"
        fourthline = str(self.scanparams)+"\n"
        fifthline = "ions"
        sixthline = "["+str(self.ions[0])+", "+...+"]"
    """
    f = file(filename)
    firstline = f.readline()
    secondline = f.readline()[:-1]
    thirdline = f.readline()
    fourthline = f.readline()[:-1]
    fifthline = f.readline()
    sixthline = f.readline()[:-1]
    simparams = eval(secondline)
    scanparams = eval(fourthline)
    ions = sixthline[1:-1].split(", ")
    length = scanparams[5]
    data = np.zeros((int(length),11+len(ions)+1))
    for i in range(length):
        myline = f.readline()[1:-1]
        data[i] = np.fromstring(myline,sep = " ")
    return simparams,scanparams,ions,data


def _get_coldens_helper(dsparamsvectorions):
    try:
        ds = dsparamsvectorions[0]
        scanparams = dsparamsvectorions[1]
        vector = dsparamsvectorions[2]
        ions = dsparamsvectorions[3]
        print(str(current_process()))
        ident = str(current_process()).split(",")[0]
        if ident[-2:] == "ss":
            ident = ""
        else:
            ident = ident.split("-")[1]
        start = vector[5:8]
        end = vector[8:11]        
        ray = trident.make_simple_ray(ds,
                    start_position=start,
                    end_position=end,
                    data_filename="ray"+ident+".h5",
                    fields = [('gas',"metallicity")],
                    ftype='gas')
        trident.add_ion_fields(ray,ions)
        field_data = ray.all_data()
        for i in range(len(ions)):
            ion = ions[i]
            cdens = np.sum(field_data[("gas",ion_to_field_name(ion))] * field_data['dl'])
            #outcdens = np.sum((field_data['radial_velocity']>0)*field_data[ion_to_field_name(ion)]*field_data['dl'])
            #incdens = np.sum((field_data['radial_velocity']<0)*field_data[ion_to_field_name(ion)]*field_data['dl'])
            vector[11+i] = cdens
            #vector[12+3*i+1] = outcdens
            #vector[12+3*i+2] = incdens
        Z = np.average(field_data[('gas',"metallicity")],weights=field_data['dl'])
        vector[-1] = Z
        if _platform == 'darwin':
            foldername = "/Users/claytonstrawn/Desktop/astroresearch/code/ready_for_pleiades/quasarlines"
        else:
            foldername = "/u/cstrawn/quasarlines/galaxy_catalogs/"
    except Exception:
        logging.exception("failed")
    try:
        os.remove(foldername+"/"+"ray"+ident+".h5")
    except:
        pass 
    print("vector = "+str(vector))
    return vector

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
#white (255,255,255), yellow (255,255,0), orange (255,165,0), red (255,0,0), darkred (139,0,0), black (0,0,0)
f= 256.0
cdict = {'red':   ((0.0,  255/f, 255/f),
                   (0.01, 255/f, 255/f),
                   (0.5,  255/f, 255/f),
                   (0.6,  255/f, 255/f),
                   (0.7,  139/f, 139/f),
                   (1.0,  0/f, 0/f)),

         'green': ((0.0,  255/f, 255/f),
                   (0.01, 255/f, 255/f),
                   (0.5,  165/f, 165/f),
                   (0.6,  0/f, 0/f),
                   (0.7,  0/f, 0/f),
                   (1.0,  0/f, 0/f)),

         'blue':  ((0.0,  255/f, 255/f),
                   (0.01, 255/f, 0/f),
                   (0.5,  0/f, 0/f),
                   (0.6,  0/f, 0/f),
                   (0.7,  0/f, 0/f),
                   (1.0,  0/f, 0/f))}

hotcustom = LinearSegmentedColormap('HotCustom', cdict)
plt.register_cmap(cmap=hotcustom)

def plot2dhist(ion,xvars,cdens,simname,xvar = "r",ns = (42,15),zeros = "ignore",weights = True, save_fig = None):
    if zeros == "ignore":
        xvars = xvars[cdens>0]
        cdens = cdens[cdens>0]
        logdens = np.log10(cdens)
    else:
        logdens = np.log10(np.maximum(cdens,1e-15))
    print xvars.shape,cdens.shape
    nx = ns[0]
    ny = ns[1]
    if weights:
        weight = xvars*0.0
        for i in range(len(xvars)):
            weight[i] = 1.0/len(xvars[xvars==xvars[i]])
        H, xedges, yedges = np.histogram2d(xvars, logdens, bins=[nx,ny],weights = weight)
        cbarlabel = "Fraction of lines for fixed %s"%(xvar)
    else:
        H, xedges, yedges = np.histogram2d(rs, logdens, bins=[nx,ny])
        cbarlabel = "Total number of lines"
    H = H.T
    X, Y = np.meshgrid(xedges, yedges)
    plt.pcolormesh(X,Y, H, cmap=hotcustom)
    plt.title("distribution of "+ion+" in "+simname)
    # set the limits of the plot to the limits of the data
    #plt.axis([x.min(), x.max(), y.min(), y.max()])
    plt.colorbar(label = cbarlabel)
    x1,x2,y1,y2 = plt.axis()
    dx = x2-x1
    dy = y2-y1
    plt.axis((x1-dx*0.1,x2+dx*0.1,y1-dy*0.1,y2+dy*0.1))
    xlabels = {"r":"r (kpc)","theta":"viewing angle (rad)","phi":"azimuthal viewing angle (rad)"}
    plt.xlabel(xlabels[xvar])
    plt.ylabel("log col dens")
    if save_fig:
        name = save_fig+"_"+ion.replace(" ","")
        if weights:
            name +="_w"
        if zeros == "ignore":
            name +="_nozeros"
        plt.savefig(name+".png")
    plt.show()

#R,lat_n,r_n,long_dx,alpha_dx, center = None, largest_r = None,length = None,distances = "kpc",starting_guess = 50000):
#    def create_QSO_endpoints(self,R,lat_n,r_n,long_dx,alpha_dx, largest_r, center = None, \

def convert_a0_to_redshift(a0):
    return 1.0/float(a0)-1

def read_Choi_metadata():
    files = [948,908,858,763,721,664,616,549,501,408,380,329,305,290,259,227,224,220,215,209,204,190,189,175,163,162,125,53]
    all_data_dict = {}
    for num in files:
        filename = "/Users/claytonstrawn/Downloads/Choi17_z1set/m0{0:03d}_info_044.txt".format(num)
        f = file(filename)
        lines = f.readlines()
        data_dict = {}
        for line in lines:
            if len(line.split(":"))>1:
                key,value = line.split(":")
                data_dict[key] = value
        all_data_dict[num] = data_dict
    return files,all_data_dict

if __name__ == "__main__":
	new = sys.argv[1]
	if new == "n":	
		dspath = sys.argv[2]
		simname = sys.argv[3]
		ions = sys.argv[4]
		scanparams = sys.argv[5]
		# R, n_th, n_phi, n_r, rmax
		scan_methods = sys.argv[6]
		# length, save, parallel
		q = QuasarSphere(simname = simname ,dspath = dspath)
	elif new == "c":
		filename = sys.argv[2]
		simparams,scanparams,ions,data = read_values(filename)

		q = QuasarSphere(simparams = simparams,scanparams = scanparams,ions,data)
		simparams,scanparams,ions,data




