# Image subset for the Image Converter workloads

Taken from the previous paper's dataset (`generated-software-llm/data/images`, 6 breeds, 1,201 images,
120 MB), which the paper's JMeter plan (`reference/image_converter.jmx`) sampled at random per breed.
To keep the bundle small, this subset holds the first 10 images of each breed in natural sort order
(60 images). The R0 workload samples from this subset instead of the full dataset.
