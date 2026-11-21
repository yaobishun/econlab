function econlab_qse(input_file, request_file, output_file)
% Adapter only. Economic equations are the unmodified author functions.
global alpha sigma LL LLwest LLeast F;
S=load(input_file); Q=jsondecode(fileread(request_file));
alpha=S.alpha; sigma=S.sigma; LL=S.LL; LLwest=S.LLwest; LLeast=S.LLeast; F=S.F;
addpath(fullfile(fileparts(mfilename('fullpath')),'author','functions'));
maxNumCompThreads(4);
[w,L,tradesh,dtradesh,converge,~]=solveHLwCtyOpen_E(S.fund,S.dist,S.bord,S.bordcty,S.NN);
if converge~=1,error('Baseline did not converge');end
P=Hpindex(S.fund,L,w,dtradesh);r=Hlandprice(S.fund,L,w);realwage=Hrealw(S.fund,L,w,tradesh);
if isfield(Q,'validation') && Q.validation
 [cw,cL,ctradesh,cdtradesh,ok1,~]=solveHLwCtyOpen_E(S.fund,S.dist,S.bord,S.cbordcty,S.NN);
 [ccw,ccL,cctradesh,ccdtradesh,ok2,~]=solveHLwCtyOpen_E(S.fund,S.dist,S.cbord,S.bordcty,S.NN);
 if ok1~=1 || ok2~=1,error('Counterfactual did not converge');end
 cP=Hpindex(S.fund,cL,cw,cdtradesh);cr=Hlandprice(S.fund,cL,cw);crealwage=Hrealw(S.fund,cL,cw,ctradesh);
 ccP=Hpindex(S.fund,ccL,ccw,ccdtradesh);ccr=Hlandprice(S.fund,ccL,ccw);ccrealwage=Hrealw(S.fund,ccL,ccw,cctradesh);
 save(output_file,'w','L','P','r','realwage','cw','cL','cP','cr','crealwage','ccw','ccL','ccP','ccr','ccrealwage','converge','ok1','ok2','-v7');
else
 country=S.bordcty; local=S.bord;
 country=1+(country-1)*(1-Q.country_reduction/100);
 local=1+(local-1)*(1-Q.location_reduction/100);
 [cw,cL,ctradesh,cdtradesh,ok1,~]=solveHLwCtyOpen_E(S.fund,S.dist,local,country,S.NN);
 if ok1~=1,error('Counterfactual did not converge');end
 cP=Hpindex(S.fund,cL,cw,cdtradesh);cr=Hlandprice(S.fund,cL,cw);crealwage=Hrealw(S.fund,cL,cw,ctradesh);
 welfare=Hwelfaregains(ctradesh,tradesh,cL,L);
 west=S.fund(:,3)==1; east=S.fund(:,4)==1;
 welfare_west_pct=100*(mean(welfare(west))-1);welfare_east_pct=100*(mean(welfare(east))-1);
 population_reallocated_pct=100*sum(abs(cL-L))/(2*S.LL);
 save(output_file,'w','L','P','r','realwage','cw','cL','cP','cr','crealwage','welfare','welfare_west_pct','welfare_east_pct','population_reallocated_pct','converge','ok1','-v7');
end
end
