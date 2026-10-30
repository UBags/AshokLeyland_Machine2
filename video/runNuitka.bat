@echo Started: %date% %time%
" remove --enable-console later; add --enable-plugin=pyside6
" pip install transformers theano tensorflow keras ipykernel ipython-genutils ipython tensorflow-estimator tensorflow-intel tensorflow-io-gcs-filesystem ipywidget numba spacy torch
" pip install spacy-alignments spacy-conll spacy-crfsuite spacy-legacy spacy-loggers spacy-stanza spacy-syllabus spacy-transformers spacy-wrap torchvision torchaudio torch-ac
" pip install torch-bnn torch-checker torch-constrained torch-discounted-cumsum torch-dreams torch-explain torch-geometric torch-hd torch-light torch-pitch-shift
" nuitka --standalone --follow-imports --assume-yes-for-downloads --remove-output --force-dll-dependency-cache-update --enable-plugin=tk-inter --noinclude-setuptools-mode=nofollow non_PTZ_Hikvision.py
" nuitka --standalone --follow-imports --assume-yes-for-downloads --remove-output --force-dll-dependency-cache-update --enable-plugin=tk-inter --noinclude-setuptools-mode=nofollow --noinclude-unittest-mode=nofollow --noinclude-pytest-mode=nofollow --noinclude-IPython-mode=nofollow --enable-console non_PTZ_Hikvision.py
nuitka --standalone --follow-imports --assume-yes-for-downloads --remove-output --force-dll-dependency-cache-update --noinclude-setuptools-mode=nofollow --enable-console video/non_PTZ_Hikvision.py
@echo Ended: %date% %time%