(define (problem rebuild-09)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear d) (hand-empty) (on a c) (on c e) (on d a) (on e b) (on-table b))
  (:goal (and (on b d) (on c a) (on e b) (on-table a) (on-table d)))
)
